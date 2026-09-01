

using Agitator.Core.Entities;

namespace AgitatorDesignSuite.Web.Services
{
    public interface IReportService
    {
        /// <summary>Generates a PDF datasheet for the project and returns the saved file path.</summary>
        string GenerateDatasheet(Project project);
    }

    public class ReportGenerationOptions
    {
        public string OutputDirectory { get; set; } = string.Empty;
        public string CompanyName { get; set; } = string.Empty;
        public string? CompanyLogoPath { get; set; }
    }
}
