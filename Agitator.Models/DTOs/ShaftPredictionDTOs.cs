using System.Text.Json.Serialization;

namespace Agitator.Models.DTOs
{
    /// <summary>Options bound from appsettings "ShaftPredictionService".</summary>
    public class ShaftPredictionOptions
    {
        public string BaseUrl { get; set; } = "http://127.0.0.1:8001";
        public int TimeoutSeconds { get; set; } = 10;
        public int RetryCount { get; set; } = 1;

        /// <summary>Maximum allowed gap (%) between PINN and formula before "not trusted".</summary>
        public double TolerancePercent { get; set; } = 10.0;
    }

    /// <summary>Request sent to POST /predict. Units: N*m and metres. Names match the Python service exactly.</summary>
    public class ShaftPredictionRequest
    {
        [JsonPropertyName("ratedTorqueNm")]
        public double RatedTorqueNm { get; set; }

        [JsonPropertyName("impellerDiameterM")]
        public double ImpellerDiameterM { get; set; }

        [JsonPropertyName("liquidHeightM")]
        public double LiquidHeightM { get; set; }


        [JsonPropertyName("allowableShearStressMpa")]
        public double AllowableShearStressMpa { get; set; }

        [JsonPropertyName("operatingTemperatureC")]
        public double OperatingTemperatureC { get; set; }

        [JsonPropertyName("shaftMaterialName")]
        public string ShaftMaterialName { get; set; } = string.Empty;


        [JsonPropertyName("operatingSpeedRpm")]
        public double OperatingSpeedRpm { get; set; }

        [JsonPropertyName("impellerMassKg")]
        public double? ImpellerMassKg { get; set; }

        [JsonPropertyName("elasticModulusGpa")]
        public double ElasticModulusGpa { get; set; }

        [JsonPropertyName("poissonRatio")]
        public double PoissonRatio { get; set; }

        [JsonPropertyName("densityKgM3")]
        public double DensityKgM3 { get; set; }

        [JsonPropertyName("yieldStrengthMpa")]
        public double YieldStrengthMpa { get; set; }

        [JsonPropertyName("ultimateStrengthMpa")]
        public double UltimateStrengthMpa { get; set; }

        [JsonPropertyName("enduranceLimitMpa")]
        public double? EnduranceLimitMpa { get; set; }

        [JsonPropertyName("surfaceFinish")]
        public string SurfaceFinish { get; set; } = "machined";
    }

    /// <summary>Raw response from the Python service. Names match the Python response exactly.</summary>
    public class ShaftPredictionServiceResponse
    {
        [JsonPropertyName("predictedShaftDiameterMm")]
        public double PredictedShaftDiameterMm { get; set; }

        [JsonPropertyName("rawPredictedDiameterMm")]
        public double RawPredictedDiameterMm { get; set; }

        [JsonPropertyName("formulaShaftDiameterMm")]
        public double FormulaShaftDiameterMm { get; set; }


        [JsonPropertyName("rawFormulaDiameterMm")]
        public double RawFormulaDiameterMm { get; set; }

        [JsonPropertyName("gapPercent")]
        public double GapPercent { get; set; }

        [JsonPropertyName("shearStressMpa")]
        public double ShearStressMpa { get; set; }

        [JsonPropertyName("allowableShearStressMpa")]
        public double AllowableShearStressMpa { get; set; }

        [JsonPropertyName("withinTrustedRange")]
        public bool WithinTrustedRange { get; set; }

        [JsonPropertyName("modelVersion")]
        public string ModelVersion { get; set; } = string.Empty;

        [JsonPropertyName("notes")]
        public string Notes { get; set; } = string.Empty;


        [JsonPropertyName("shaftLengthM")]
        public double ShaftLengthM { get; set; }

        [JsonPropertyName("physics")]
        public ShaftPhysicsServiceBlock? Physics { get; set; }
    }

    /// <summary>Final result stored as JSON on CalculationResult.PredictionJson and shown in the view.</summary>
    public class ShaftPredictionResult
    {
        public double RawFormulaDiameterMm { get; set; }
        public double FormulaShaftDiameterMm { get; set; }
        public double PredictedShaftDiameterMm { get; set; }
        public double RawPredictedDiameterMm { get; set; }
        public double GapPercent { get; set; }
        public double ShearStressMpa { get; set; }
        public double AllowableShearStressMpa { get; set; }
        public bool IsTrusted { get; set; }
        public string Notes { get; set; } = string.Empty;
        public string ModelVersion { get; set; } = string.Empty;
        public DateTime GeneratedAtUtc { get; set; }
        public string ShaftMaterialName { get; set; } = string.Empty;
        public string HeatTreatment { get; set; } = string.Empty;
        public double OperatingTemperatureC { get; set; }
        public double YieldStrengthAtTempMpa { get; set; }
        public double ShaftLengthM { get; set; }
        public double OperatingSpeedRpm { get; set; }

        /// <summary>PINN deflection, twist, stresses, critical speed and fatigue factors from the Python service (null for results saved before this feature).</summary>
        public ShaftPhysicsServiceBlock? Physics { get; set; }

        /// <summary>Manual vs FEA vs PINN comparison and design checks (null for results saved before this feature).</summary>
        public ShaftComparisonResult? Comparison { get; set; }

    }
}