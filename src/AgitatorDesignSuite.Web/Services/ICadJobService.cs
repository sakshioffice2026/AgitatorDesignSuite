

namespace AgitatorDesignSuite.Web.Services
{
    public interface ICadJobService
    {
        /// <summary>
        /// Enqueues a CAD generation job for a project. Actual generation
        /// happens out-of-process in CadJobBackgroundService so slow FreeCAD
        /// runs never block a web request.
        /// </summary>
        Task EnqueueAsync(int projectId, CancellationToken ct = default);
    }

    public class CadIntegrationOptions
    {
        public string FreeCadCmdPath { get; set; } = string.Empty;
        public string MacroPath { get; set; } = string.Empty;
        public string OutputDirectory { get; set; } = string.Empty;
        public int PollingIntervalSeconds { get; set; } = 5;
    }
}
