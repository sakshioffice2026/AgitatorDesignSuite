using Agitator.Core.Entities;

namespace Agitator.Repositories.Contracts
{
    public interface ICalculationResultRepository
    {
        Task<CalculationResult?> GetByProjectIdAsync(int projectId);
        Task CreateAsync(CalculationResult result);
        Task UpdateCalculationAsync(CalculationResult result);
        Task<(bool success, string? error)> ChangeCadStatusAsync(int projectId, CadJobStatus status, string? error = null, string? stepFilePath = null, string? previewMeshPath = null);
        Task<(bool success, string? error)> SetReportPathAsync(int projectId, string reportPdfPath);

        Task<(bool success, string? error)> SetPredictionAsync(int projectId, string predictionJson, DateTime predictedAtUtc);
    }
}