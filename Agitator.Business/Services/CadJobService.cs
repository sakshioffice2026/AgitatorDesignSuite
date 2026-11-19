using Agitator.Business.Contracts;
using Agitator.Core.Entities;
using Agitator.Repositories.Contracts;
using Agitator.Models.DTOs;
using System.Text.Json;

namespace Agitator.Business.Services
{
    /// <summary>
    /// Web-tier half of the CAD pipeline: just flips the job to "Queued" via
    /// the repository. The actual FreeCAD process invocation lives in
    /// CadJobBackgroundService so it never runs on a web request thread.
    /// </summary>
    public class CadJobService : ICadJobService
    {
        private readonly IUnitOfWork _uow;

        public CadJobService(IUnitOfWork uow)
        {
            _uow = uow;
        }
        
       public async Task<(bool success, string? error)> EnqueueAsync(int projectId, CadSource? source, CancellationToken ct = default)
        {
            var existing = await _uow.calculationResultRepository.GetByProjectIdAsync(projectId);
            if (existing is null)
                return (false, $"No calculation result found for project {projectId}. Run calculations before requesting a CAD model.");

            double? diameterMm = null;
            if (source.HasValue)
            {
                var (ok, resolvedMm, resolveError) = ResolveDiameterMm(existing, source.Value);
                if (!ok)
                    return (false, resolveError);

                diameterMm = resolvedMm;
            }

            var (saved, saveError) = await _uow.calculationResultRepository.SetCadSourceAsync(projectId, source, diameterMm);
            if (!saved)
                return (false, saveError);

            return await _uow.calculationResultRepository.ChangeCadStatusAsync(projectId, CadJobStatus.Queued);
        }

        private static readonly JsonSerializerOptions JsonReadOptions = new()
        {
            PropertyNameCaseInsensitive = true
        };

        private static readonly double[] StandardShaftSizesMm =
        {
            20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 90, 100,
            110, 120, 130, 140, 150, 160, 180, 200, 220, 240, 260, 280, 300
        };

        private static (bool ok, double diameterMm, string? error) ResolveDiameterMm(CalculationResult result, CadSource source)
        {
            if (source == CadSource.Manual)
                return (true, result.RecommendedShaftDiameterMm, null);

            ShaftComparisonResult? comparison = null;
            if (!string.IsNullOrWhiteSpace(result.ComparisonJson))
            {
                try
                {
                    comparison = JsonSerializer.Deserialize<ShaftComparisonResult>(result.ComparisonJson, JsonReadOptions);
                }
                catch (JsonException)
                {
                    comparison = null;
                }
            }

            var quantity = comparison?.Quantities
                .FirstOrDefault(q => q.Name == "Shaft diameter (before rounding)");

            double? rawMm = source == CadSource.Fea ? quantity?.Fea : quantity?.Pinn;

            if (!rawMm.HasValue || rawMm.Value <= 0)
            {
                return (false, 0.0, source == CadSource.Fea
                    ? "FEA result is not available. Run the FEA check before generating the STEP file from FEA."
                    : "PINN result is not available. Generate the shaft prediction before generating the STEP file from PINN.");
            }

            return (true, RoundUpToStandardSizeMm(rawMm.Value), null);
        }

        private static double RoundUpToStandardSizeMm(double mm)
        {
            foreach (double size in StandardShaftSizesMm)
            {
                if (mm <= size) return size;
            }

            return Math.Ceiling(mm / 10.0) * 10.0;
        }
    }
}