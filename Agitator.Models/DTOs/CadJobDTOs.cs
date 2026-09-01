namespace Agitator.Models.DTOs
{
    /// <summary>Options bound from appsettings for the FreeCAD integration (external/service config, not persisted).</summary>
    public class CadIntegrationOptions
    {
        public string FreeCadCmdPath { get; set; } = string.Empty;
        public string MacroPath { get; set; } = string.Empty;
        public string OutputDirectory { get; set; } = string.Empty;
        public int PollingIntervalSeconds { get; set; } = 5;
    }

    /// <summary>Options bound from appsettings for PDF report generation.</summary>
    public class ReportGenerationOptions
    {
        public string OutputDirectory { get; set; } = string.Empty;
        public string CompanyName { get; set; } = string.Empty;
        public string? CompanyLogoPath { get; set; }
    }
}