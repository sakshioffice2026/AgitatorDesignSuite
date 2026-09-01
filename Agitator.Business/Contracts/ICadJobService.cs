namespace Agitator.Business.Contracts
{
    public interface ICadJobService
    {
        /// <summary>
        /// Enqueues a CAD generation job for a project. Actual generation
        /// happens out-of-process in CadJobBackgroundService so slow FreeCAD
        /// runs never block a web request.
        /// </summary>
        Task<(bool success, string? error)> EnqueueAsync(int projectId, CancellationToken ct = default);
    }
}