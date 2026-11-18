using System.Text.Json.Serialization;

namespace Agitator.Models.DTOs
{
    /// <summary>Limits and tolerances for the shaft comparison. Bound from appsettings "ShaftComparison".</summary>
    public class ShaftComparisonOptions
    {
        /// <summary>Allowed gap (%) between PINN and the reference for the shaft diameter.</summary>
        public double DiameterTolerancePercent { get; set; } = 5.0;

        /// <summary>Allowed gap (%) between PINN and the reference for deflection.</summary>
        public double DeflectionTolerancePercent { get; set; } = 5.0;

        /// <summary>Allowed gap (%) between PINN and the reference for angle of twist.</summary>
        public double TwistTolerancePercent { get; set; } = 5.0;

        /// <summary>Tip deflection limit as a fraction of shaft length (0.001 = 1 mm per metre).</summary>
        public double DeflectionLimitRatio { get; set; } = 0.001;

        /// <summary>Operating speed must stay below this fraction of the first critical speed.</summary>
        public double MaxCriticalSpeedRatio { get; set; } = 0.7;

        /// <summary>Required Goodman / Soderberg safety factor.</summary>
        public double RequiredFatigueFactor { get; set; } = 1.5;
    }

    /// <summary>Which design check a <see cref="ShaftCheckResult"/> describes.</summary>
    public enum ShaftCheckKind
    {
        Strength = 0,
        Deflection = 1,
        CriticalSpeed = 2,
        Fatigue = 3
    }

    /// <summary>Extra physics values returned by the Python service next to the diameter. Names match the service.</summary>
    public class ShaftPhysicsServiceBlock
    {
        [JsonPropertyName("tipDeflectionMm")]
        public double TipDeflectionMm { get; set; }

        [JsonPropertyName("twistDeg")]
        public double TwistDeg { get; set; }

        [JsonPropertyName("rootBendingMomentNm")]
        public double RootBendingMomentNm { get; set; }

        [JsonPropertyName("maxShearStressMpa")]
        public double MaxShearStressMpa { get; set; }

        [JsonPropertyName("vonMisesStressMpa")]
        public double VonMisesStressMpa { get; set; }

        [JsonPropertyName("criticalSpeedRpm")]
        public double CriticalSpeedRpm { get; set; }

        [JsonPropertyName("goodmanFactor")]
        public double GoodmanFactor { get; set; }

        [JsonPropertyName("soderbergFactor")]
        public double SoderbergFactor { get; set; }

        [JsonPropertyName("yieldFactor")]
        public double YieldFactor { get; set; }

        [JsonPropertyName("pdeResidual")]
        public double PdeResidual { get; set; }
    }

    /// <summary>FEA values for the project. Names match fea_result.json written by fea_check_shaft.py.</summary>
    public class ShaftFeaResult
    {
        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("feaMaxShearMpa")]
        public double? MaxShearMpa { get; set; }

        [JsonPropertyName("feaVonMisesMpa")]
        public double? VonMisesMpa { get; set; }

        [JsonPropertyName("feaTipDeflectionMm")]
        public double? TipDeflectionMm { get; set; }

        [JsonPropertyName("feaTwistDeg")]
        public double? TwistDeg { get; set; }

        [JsonPropertyName("allowableShearMpa")]
        public double? AllowableShearMpa { get; set; }

        [JsonPropertyName("utilization")]
        public double? Utilization { get; set; }

        [JsonPropertyName("passed")]
        public bool? Passed { get; set; }

        [JsonPropertyName("meshNodes")]
        public int? MeshNodes { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }

        [JsonIgnore]
        public bool IsOk => string.Equals(Status, "ok", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>One calculated quantity shown side by side: manual formula, FEA and PINN.</summary>
    public class ShaftQuantityComparison
    {
        public string Name { get; set; } = string.Empty;
        public string Unit { get; set; } = string.Empty;
        public double? Manual { get; set; }
        public double? Fea { get; set; }
        public double? Pinn { get; set; }
        public double TolerancePercent { get; set; }

        /// <summary>FEA when it was run, otherwise the manual value.</summary>
        public string ReferenceName => Fea.HasValue ? "FEA" : "Manual";

        public double? Reference => Fea ?? Manual;

        public double? PinnVsManualPercent => ShaftComparisonMath.PercentDifference(Pinn, Manual);
        public double? PinnVsFeaPercent => ShaftComparisonMath.PercentDifference(Pinn, Fea);
        public double? ManualVsFeaPercent => ShaftComparisonMath.PercentDifference(Manual, Fea);

        /// <summary>True when PINN is inside the tolerance of the reference; null when it cannot be judged.</summary>
        public bool? PinnAgrees
        {
            get
            {
                double? gap = ShaftComparisonMath.PercentDifference(Pinn, Reference);
                return gap.HasValue ? Math.Abs(gap.Value) <= TolerancePercent : null;
            }
        }
    }

    /// <summary>One design check. Demand is what the shaft sees, Capacity is what it can take.</summary>
    public class ShaftCheckResult
    {
        public ShaftCheckKind Kind { get; set; }
        public string Name { get; set; } = string.Empty;
        public string Unit { get; set; } = string.Empty;
        public double DemandValue { get; set; }
        public double CapacityValue { get; set; }
        public string Source { get; set; } = string.Empty;

        /// <summary>Demand divided by capacity. 1.0 means the check is exactly at its limit.</summary>
        public double Utilization => CapacityValue > 0 ? DemandValue / CapacityValue : double.PositiveInfinity;

        /// <summary>Spare capacity in percent. Negative when the check fails.</summary>
        public double MarginPercent => (1.0 - Utilization) * 100.0;

        public bool Passed => Utilization <= 1.0;
    }

    /// <summary>Complete result shown on the Results page: three-way comparison plus all design checks.</summary>
    public class ShaftComparisonResult
    {
        public double StandardShaftSizeMm { get; set; }
        public bool FeaAvailable { get; set; }
        public string ModelVersion { get; set; } = string.Empty;
        public double PdeResidual { get; set; }
        public DateTime GeneratedAtUtc { get; set; }

        public List<ShaftQuantityComparison> Quantities { get; set; } = new();
        public List<ShaftCheckResult> Checks { get; set; } = new();
        public List<string> Notes { get; set; } = new();

        public bool AllChecksPassed => Checks.Count > 0 && Checks.All(c => c.Passed);

        /// <summary>True when every judged quantity has PINN inside tolerance. Quantities that cannot be judged are ignored.</summary>
        public bool AllQuantitiesAgree =>
            Quantities.Any(q => q.PinnAgrees.HasValue) &&
            Quantities.Where(q => q.PinnAgrees.HasValue).All(q => q.PinnAgrees == true);

        public bool OverallPassed => AllChecksPassed && AllQuantitiesAgree;

        /// <summary>The check closest to (or furthest over) its limit.</summary>
        public ShaftCheckResult? GoverningCheck =>
            Checks.Count == 0 ? null : Checks.OrderByDescending(c => c.Utilization).First();
    }

    /// <summary>Shared helper for the comparison classes.</summary>
    public static class ShaftComparisonMath
    {
        /// <summary>(value - reference) / reference in percent. Null when either value is missing or the reference is zero.</summary>
        public static double? PercentDifference(double? value, double? reference)
        {
            if (!value.HasValue || !reference.HasValue || reference.Value == 0.0)
            {
                return null;
            }

            return (value.Value - reference.Value) / reference.Value * 100.0;
        }
    }
}
