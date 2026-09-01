namespace Agitator.Utilities.Helpers
{
    public class BreadcrumbItem
    {
        public string Text { get; set; } = string.Empty;
        public string? Url { get; set; }
        public bool IsActive { get; set; }
    }
}