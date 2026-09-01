
using Agitator.Core;
using Agitator.Core.Entities;
using Microsoft.EntityFrameworkCore;

namespace AgitatorDesignSuite.Web.Services
{
    /// <summary>
    /// Web-tier half of the CAD pipeline: just flips the job to "Queued" in
    /// MySQL. The actual FreeCAD process invocation lives in
    /// CadJobBackgroundService so it never runs on a web request thread.
    /// </summary>
    public class CadJobService : ICadJobService
    {
        private readonly ApplicationDbContext _db;

        public CadJobService(ApplicationDbContext db)
        {
            _db = db;
        }

        public async Task EnqueueAsync(int projectId, CancellationToken ct = default)
        {
            var result = await _db.CalculationResults
                .FirstOrDefaultAsync(r => r.ProjectId == projectId, ct);

            if (result is null)
                throw new InvalidOperationException(
                    $"No calculation result found for project {projectId}. Run calculations before requesting a CAD model.");

            result.CadStatus = CadJobStatus.Queued;
            result.CadJobError = null;
            await _db.SaveChangesAsync(ct);
        }
    }
}
