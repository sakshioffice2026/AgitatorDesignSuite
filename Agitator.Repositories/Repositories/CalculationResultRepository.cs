using Agitator.Core;
using Agitator.Core.Entities;
using Agitator.Repositories.Contracts;
using Microsoft.EntityFrameworkCore;

namespace Agitator.Repositories.Repositories
{
    public class CalculationResultRepository : ICalculationResultRepository
    {
        private readonly ApplicationDbContext _db;

        public CalculationResultRepository(ApplicationDbContext db)
        {
            _db = db;
        }

        public async Task<CalculationResult?> GetByProjectIdAsync(int projectId)
        {
            return await _db.CalculationResults
                .FirstOrDefaultAsync(r => r.ProjectId == projectId);
        }

        public async Task CreateAsync(CalculationResult result)
        {
            _db.CalculationResults.Add(result);
            await _db.SaveChangesAsync();
        }

        public async Task<(bool success, string? error)> ChangeCadStatusAsync(
            int projectId, CadJobStatus status, string? error = null,
            string? stepFilePath = null, string? previewMeshPath = null)
        {
            var result = await _db.CalculationResults
                .FirstOrDefaultAsync(r => r.ProjectId == projectId);

            if (result is null)
                return (false, $"No calculation result found for project {projectId}.");

            result.CadStatus = status;
            if (error is not null) result.CadJobError = error;
            if (stepFilePath is not null) result.CadStepFilePath = stepFilePath;
            if (previewMeshPath is not null) result.CadPreviewMeshPath = previewMeshPath;

            await _db.SaveChangesAsync();
            return (true, null);
        }

        public async Task<(bool success, string? error)> SetReportPathAsync(int projectId, string reportPdfPath)
        {
            var result = await _db.CalculationResults
                .FirstOrDefaultAsync(r => r.ProjectId == projectId);

            if (result is null)
                return (false, $"No calculation result found for project {projectId}.");

            result.ReportPdfPath = reportPdfPath;
            await _db.SaveChangesAsync();
            return (true, null);
        }
    }
}