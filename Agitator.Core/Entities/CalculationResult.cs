using System.ComponentModel.DataAnnotations.Schema;

namespace Agitator.Core.Entities
{
    public enum FlowRegime
    {
        Laminar,
        Transitional,
        Turbulent
    }

    public enum CadJobStatus
    {
        NotRequested,
        Queued,
        Running,
        Completed,
        Failed
    }

    /// <summary>
    /// Persisted output of the calculation engine for a project, plus the
    /// status of any downstream CAD generation job.
    /// </summary>
    public class CalculationResult
    {
        public int Id { get; set; }

        [ForeignKey(nameof(Project))]
        public int ProjectId { get; set; }
        public Project? Project { get; set; }

        public double ReynoldsNumber { get; set; }
        public FlowRegime Regime { get; set; }
        public double PowerNumber { get; set; }

        public double PowerDrawWatts { get; set; }
        public double TorqueNm { get; set; }
        public double TipSpeedMPerS { get; set; }

        public double RecommendedShaftDiameterMm { get; set; }
        public double RecommendedMotorPowerKw { get; set; }

        public DateTime CalculatedAtUtc { get; set; } = DateTime.UtcNow;

        // --- CAD job tracking ---
        public CadJobStatus CadStatus { get; set; } = CadJobStatus.NotRequested;
        public string? CadStepFilePath { get; set; }
        public string? CadPreviewMeshPath { get; set; }
        public string? CadJobError { get; set; }

        // --- Report tracking ---
        public string? ReportPdfPath { get; set; }


        // --- Shaft PINN prediction ---
        public string? PredictionJson { get; set; }
        public DateTime? PredictedAt { get; set; }


        // --- Shaft FEA check ---
        public string? FeaCheckJson { get; set; }
        public DateTime? FeaCheckedAt { get; set; }

        // --- Shaft comparison (manual vs FEA vs PINN) and design checks ---
        public string? ComparisonJson { get; set; }
        public DateTime? ComparedAt { get; set; }

        /// <summary>Tip deflection from the manual cantilever formula (mm).</summary>
        public double? TipDeflectionMm { get; set; }

        /// <summary>Angle of twist from the manual formula (degrees).</summary>
        public double? TwistDeg { get; set; }

        /// <summary>First lateral critical speed (rpm).</summary>
        public double? CriticalSpeedRpm { get; set; }

        /// <summary>Goodman fatigue safety factor.</summary>
        public double? GoodmanFactor { get; set; }

        /// <summary>True when every design check and comparison passed.</summary>
        public bool? ComparisonPassed { get; set; }
    }
}