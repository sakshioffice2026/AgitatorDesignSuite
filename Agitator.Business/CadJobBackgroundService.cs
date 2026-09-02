using System.Diagnostics;
using System.Text.Json;
using Agitator.Core;
using Agitator.Core.Entities;
using Agitator.Models.DTOs;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Agitator.Business
{
    /// <summary>
    /// Polls MySQL for queued CAD jobs and runs the parametric macro via
    /// FreeCAD's bundled Python interpreter, isolating the (slow, CPU
    /// heavy) CAD generation from the web request pipeline.
    ///
    /// NOTE: CadIntegrationOptions.FreeCadCmdPath should point at FreeCAD's
    /// bundled python.exe (e.g. "...\FreeCAD 1.1\bin\python.exe"), not
    /// freecadcmd.exe — as of FreeCAD 1.x, freecadcmd no longer
    /// auto-executes a .py file passed on the command line (it just drops
    /// to an interactive console instead); running the macro through
    /// FreeCAD's own python.exe avoids that regression.
    ///
    /// Uses ApplicationDbContext directly (scoped per poll) rather than the
    /// repository layer since it runs outside any web request/DI scope as a
    /// long-lived hosted service — a pragmatic exception for this
    /// integration-heavy background worker.
    ///
    /// Run this as its own Windows Service / systemd unit in production
    /// rather than in-process with the web app once load grows — it's
    /// registered as a hosted service here to keep the starter scaffold to
    /// a single deployable for now.
    /// </summary>
    public class CadJobBackgroundService : BackgroundService
    {
        private readonly IServiceScopeFactory _scopeFactory;
        private readonly CadIntegrationOptions _options;
        private readonly ILogger<CadJobBackgroundService> _logger;

        public CadJobBackgroundService(
            IServiceScopeFactory scopeFactory,
            IOptions<CadIntegrationOptions> options,
            ILogger<CadJobBackgroundService> logger)
        {
            _scopeFactory = scopeFactory;
            _options = options.Value;
            _logger = logger;
        }

        protected override async Task ExecuteAsync(CancellationToken stoppingToken)
        {
            Directory.CreateDirectory(_options.OutputDirectory);

            while (!stoppingToken.IsCancellationRequested)
            {
                try
                {
                    await ProcessNextQueuedJobAsync(stoppingToken);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "CAD job polling loop failed");
                }

                await Task.Delay(TimeSpan.FromSeconds(_options.PollingIntervalSeconds), stoppingToken);
            }
        }

        private async Task ProcessNextQueuedJobAsync(CancellationToken ct)
        {
            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();

            var job = await db.CalculationResults
                .Include(r => r.Project)
                    .ThenInclude(p => p!.Vessel)
                .Include(r => r.Project)
                    .ThenInclude(p => p!.Impeller)
                .FirstOrDefaultAsync(r => r.CadStatus == CadJobStatus.Queued, ct);

            if (job is null || job.Project?.Vessel is null || job.Project?.Impeller is null)
                return;

            job.CadStatus = CadJobStatus.Running;
            await db.SaveChangesAsync(ct);

            try
            {
                var (stepPath, meshPath) = await RunFreeCadAsync(job, ct);
                job.CadStatus = CadJobStatus.Completed;
                job.CadStepFilePath = stepPath;
                job.CadPreviewMeshPath = meshPath;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "FreeCAD generation failed for project {ProjectId}", job.ProjectId);
                job.CadStatus = CadJobStatus.Failed;
                job.CadJobError = ex.Message;
            }

            await db.SaveChangesAsync(ct);
        }

        /// <summary>
        /// Invokes: python.exe generate_agitator_model.py --params params.json
        /// The macro (see CadScripts/generate_agitator_model.py) builds a
        /// full tank/head/flange/shaft/impeller/baffle assembly with real
        /// lofted blade geometry (Part.makeLoft) per impeller type, then
        /// exports STEP + an OBJ preview mesh. A 2D TechDraw export is
        /// supported by the script but not wired up here yet (no
        /// techDrawTemplatePath is supplied — the script just skips that
        /// step and logs it, per its own main()).
        /// </summary>
        private async Task<(string stepPath, string meshPath)> RunFreeCadAsync(
            CalculationResult job, CancellationToken ct)
        {
            var vessel = job.Project!.Vessel!;
            var impeller = job.Project!.Impeller!;

            var jobDir = Path.Combine(_options.OutputDirectory, $"project_{job.ProjectId}");
            Directory.CreateDirectory(jobDir);

            // NOTE — placeholder engineering default (flagged, not silently
            // guessed): WallThicknessMm does not exist on Vessel yet. Until
            // it gains a real field (with migration + UI input), the
            // generated tank shell uses a conservative stand-in. Do NOT
            // treat CAD output as fabrication-ready until this is backed by
            // a real project input.
            const double placeholderWallThicknessMm = 6.0; // thin-wall SS default; replace with a real Vessel.WallThicknessMm field
            double shaftTotalLengthMm = vessel.LiquidHeightM * 1000 * 1.15;

            var parameters = new
            {
                tankOuterDiameterMm = vessel.DiameterM * 1000,
                shellHeightMm = vessel.LiquidHeightM * 1000,
                wallThicknessMm = placeholderWallThicknessMm,
                headType = vessel.HeadType.ToString(),
                hasBaffles = vessel.HasBaffles,
                numberOfBaffles = vessel.NumberOfBaffles,
                // The script's _IMPELLER_BUILDERS registry keys directly off
                // the same names as ImpellerType (RushtonTurbine,
                // PitchedBladeTurbine, Propeller, HydrofoilA310, AnchorFoil,
                // HelicalRibbon), so pass it straight through instead of
                // collapsing to just two types.
                impellerType = impeller.Type.ToString(),
                impellerDiameterMm = impeller.DiameterM * 1000,
                numberOfImpellers = impeller.NumberOfImpellers,
                clearanceToDiameterRatio = impeller.ClearanceToDiameterRatio,
                shaftDiameterMm = job.RecommendedShaftDiameterMm,
                shaftTotalLengthMm = shaftTotalLengthMm,
                outputStepPath = Path.Combine(jobDir, "model.step"),
                outputMeshPath = Path.Combine(jobDir, "preview.obj")
            };

            var paramsPath = Path.Combine(jobDir, "params.json");
            await File.WriteAllTextAsync(paramsPath, JsonSerializer.Serialize(parameters), ct);

            var psi = new ProcessStartInfo
            {
                FileName = _options.FreeCadCmdPath,
                Arguments = $"\"{_options.MacroPath}\" --params \"{paramsPath}\"",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };

            using var process = new Process { StartInfo = psi };
            process.Start();

            string stdout = await process.StandardOutput.ReadToEndAsync(ct);
            string stderr = await process.StandardError.ReadToEndAsync(ct);
            await process.WaitForExitAsync(ct);

            if (process.ExitCode != 0)
                throw new InvalidOperationException(
                    $"freecadcmd exited with code {process.ExitCode}. stderr: {stderr}. stdout: {stdout}");

            return (parameters.outputStepPath, parameters.outputMeshPath);
        }
    }
}