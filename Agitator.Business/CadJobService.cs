using Agitator.Business.Contracts;
using Agitator.Core.Entities;
using Agitator.Repositories.Contracts;


namespace Agitator.Business
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

        public async Task<(bool success, string? error)> EnqueueAsync(int projectId, CancellationToken ct = default)
        {
            var existing = await _uow.calculationResultRepository.GetByProjectIdAsync(projectId);
            if (existing is null)
                return (false, $"No calculation result found for project {projectId}. Run calculations before requesting a CAD model.");

            return await _uow.calculationResultRepository.ChangeCadStatusAsync(projectId, CadJobStatus.Queued);
        }
    }
}