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
    /// Polls MySQL for queued CAD jobs and runs FreeCAD headlessly
    /// (freecadcmd) against a parametric macro, isolating the (slow, CPU
    /// heavy) CAD generation from the web request pipeline.
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
        /// Invokes: freecadcmd generate_agitator_model.py --params params.json
        /// The macro (see CadScripts/generate_agitator_model.py) reads the
        /// JSON params, builds the parametric model in FreeCAD's Part
        /// workbench, and writes both a STEP file (for CAD-grade download)
        /// and a glTF mesh (for the Three.js browser preview).
        /// </summary>
        private async Task<(string stepPath, string meshPath)> RunFreeCadAsync(
            CalculationResult job, CancellationToken ct)
        {
            var vessel = job.Project!.Vessel!;
            var impeller = job.Project!.Impeller!;

            var jobDir = Path.Combine(_options.OutputDirectory, $"project_{job.ProjectId}");
            Directory.CreateDirectory(jobDir);

            var parameters = new
            {
                vesselDiameterM = vessel.DiameterM,
                liquidHeightM = vessel.LiquidHeightM,
                hasBaffles = vessel.HasBaffles,
                numberOfBaffles = vessel.NumberOfBaffles,
                impellerType = impeller.Type.ToString(),
                impellerDiameterM = impeller.DiameterM,
                numberOfImpellers = impeller.NumberOfImpellers,
                clearanceToDiameterRatio = impeller.ClearanceToDiameterRatio,
                outputStepPath = Path.Combine(jobDir, "model.step"),
                outputMeshPath = Path.Combine(jobDir, "preview.gltf")
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