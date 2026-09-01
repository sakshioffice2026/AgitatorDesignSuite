using Agitator.Business.Contracts;
using Agitator.Core.Entities;
using Agitator.Models.DTOs;
using Microsoft.Extensions.Options;
using QuestPDF.Fluent;
using QuestPDF.Helpers;
using QuestPDF.Infrastructure;


namespace Agitator.Business
{
    /// <summary>
    /// Generates an engineering datasheet PDF: design basis, geometry,
    /// fluid properties, and calculated results, with a disclaimer that
    /// this is for reference only and requires licensed-engineer review —
    /// important for a SaaS tool sold to third parties (see liability
    /// notes from the planning discussion).
    /// </summary>
    public class ReportService : IReportService
    {
        private readonly ReportGenerationOptions _options;

        public ReportService(IOptions<ReportGenerationOptions> options)
        {
            _options = options.Value;
            QuestPDF.Settings.License = LicenseType.Community;
        }

        public string GenerateDatasheet(Project project)
        {
            if (project.Vessel is null || project.Impeller is null ||
                project.FluidProperties is null || project.CalculationResult is null)
                throw new InvalidOperationException(
                    "Project must have vessel, impeller, fluid properties and a calculation result before a report can be generated.");

            Directory.CreateDirectory(_options.OutputDirectory);
            var outputPath = Path.Combine(_options.OutputDirectory, $"project_{project.Id}_datasheet.pdf");

            var vessel = project.Vessel;
            var impeller = project.Impeller;
            var fluid = project.FluidProperties;
            var result = project.CalculationResult;

            Document.Create(container =>
            {
                container.Page(page =>
                {
                    page.Size(PageSizes.A4);
                    page.Margin(2, Unit.Centimetre);
                    page.DefaultTextStyle(x => x.FontSize(10));

                    page.Header().Column(col =>
                    {
                        col.Item().Text(_options.CompanyName).FontSize(14).Bold();
                        col.Item().Text("Agitator Design Datasheet").FontSize(18).Bold();
                        col.Item().Text($"Project: {project.Name}").FontSize(11);
                        col.Item().PaddingBottom(10).LineHorizontal(1);
                    });

                    page.Content().Column(col =>
                    {
                        col.Spacing(12);

                        col.Item().Text("Vessel Geometry").Bold().FontSize(12);
                        col.Item().Table(table =>
                        {
                            table.ColumnsDefinition(c => { c.RelativeColumn(); c.RelativeColumn(); });
                            AddRow(table, "Diameter", $"{vessel.DiameterM:F2} m");
                            AddRow(table, "Liquid height", $"{vessel.LiquidHeightM:F2} m");
                            AddRow(table, "Head type", vessel.HeadType.ToString());
                            AddRow(table, "Baffles", vessel.HasBaffles ? $"{vessel.NumberOfBaffles} baffles" : "None");
                        });

                        col.Item().Text("Impeller").Bold().FontSize(12);
                        col.Item().Table(table =>
                        {
                            table.ColumnsDefinition(c => { c.RelativeColumn(); c.RelativeColumn(); });
                            AddRow(table, "Type", impeller.Type.ToString());
                            AddRow(table, "Diameter", $"{impeller.DiameterM:F3} m");
                            AddRow(table, "Speed", $"{impeller.RotationalSpeedRpm:F0} RPM");
                            AddRow(table, "Quantity", impeller.NumberOfImpellers.ToString());
                        });

                        col.Item().Text("Fluid Properties").Bold().FontSize(12);
                        col.Item().Table(table =>
                        {
                            table.ColumnsDefinition(c => { c.RelativeColumn(); c.RelativeColumn(); });
                            AddRow(table, "Density", $"{fluid.DensityKgM3:F1} kg/m3");
                            AddRow(table, "Viscosity", $"{fluid.ViscosityPaS:F4} Pa.s");
                            AddRow(table, "Behavior", fluid.IsNewtonian ? "Newtonian" : "Non-Newtonian");
                        });

                        col.Item().Text("Calculated Results").Bold().FontSize(12);
                        col.Item().Table(table =>
                        {
                            table.ColumnsDefinition(c => { c.RelativeColumn(); c.RelativeColumn(); });
                            AddRow(table, "Reynolds number", $"{result.ReynoldsNumber:F0}  ({result.Regime})");
                            AddRow(table, "Power number (Np)", $"{result.PowerNumber:F3}");
                            AddRow(table, "Power draw", $"{result.PowerDrawWatts:F0} W");
                            AddRow(table, "Torque", $"{result.TorqueNm:F2} N.m");
                            AddRow(table, "Impeller tip speed", $"{result.TipSpeedMPerS:F2} m/s");
                            AddRow(table, "Recommended shaft diameter", $"{result.RecommendedShaftDiameterMm:F1} mm");
                            AddRow(table, "Recommended motor rating", $"{result.RecommendedMotorPowerKw:F2} kW");
                        });

                        col.Item().PaddingTop(15).Background(Colors.Grey.Lighten3).Padding(8).Text(
                            "Disclaimer: This datasheet is generated for preliminary design reference only. " +
                            "All calculated values must be independently reviewed and approved by a licensed " +
                            "Professional Engineer prior to procurement, fabrication, or installation.")
                            .FontSize(8).Italic();
                    });

                    page.Footer().AlignCenter().Text(x =>
                    {
                        x.Span("Generated ").FontSize(8);
                        x.Span(DateTime.UtcNow.ToString("u")).FontSize(8);
                    });
                });
            }).GeneratePdf(outputPath);

            return outputPath;
        }

        private static void AddRow(TableDescriptor table, string label, string value)
        {
            table.Cell().Text(label).SemiBold();
            table.Cell().Text(value);
        }
    }
}