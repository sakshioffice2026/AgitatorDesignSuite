namespace Agitator.Models.ViewModel
{
    // ============================================================
    // HOME / DASHBOARD
    // ============================================================
    public class DashboardIndexViewModel
    {
        public int TotalProjects { get; set; }
        public int QueuedCadJobs { get; set; }
        public int CompletedCadJobs { get; set; }
        public List<ProjectListItemViewModel> RecentProjects { get; set; } = new();
    }
}