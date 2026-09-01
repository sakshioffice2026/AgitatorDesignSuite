using Agitator.Core.Entities;

namespace Agitator.Business.Contracts
{
    public interface IReportService
    {
        /// <summary>Generates a PDF datasheet for the project and returns the saved file path.</summary>
        string GenerateDatasheet(Project project);
    }
}