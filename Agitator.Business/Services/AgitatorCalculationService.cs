using System.Text.Json;
using Agitator.Business.Contracts;
using Agitator.Core.Entities;
using Agitator.Models.DTOs;
using Microsoft.Extensions.Options;

namespace Agitator.Business.Services
{
    /// <summary>
    /// Core process + mechanical calculation engine.
    /// Coefficients are representative published values; validate against
    /// vendor curves / test data before real equipment sizing.
    /// </summary>
    public class AgitatorCalculationService : IAgitatorCalculationService
    {
        private readonly record struct ImpellerData(
            double Kp,
            double Ct,
            bool CloseClearance,
            double LaminarReCutoff,
            double TurbulentReCutoff);

        private static readonly Dictionary<ImpellerType, ImpellerData> Coefficients = new()
        {
            [ImpellerType.RushtonTurbine] = new(70.0, 5.00, false, 10.0, 10_000.0),
            [ImpellerType.PitchedBladeTurbine] = new(70.0, 1.27, false, 10.0, 10_000.0),
            [ImpellerType.Propeller] = new(41.0, 0.32, false, 10.0, 10_000.0),
            [ImpellerType.HydrofoilA310] = new(65.0, 0.30, false, 10.0, 10_000.0),
            [ImpellerType.AnchorFoil] = new(300.0, 0.35, true, 100.0, 10_000.0),
            [ImpellerType.HelicalRibbon] = new(300.0, 0.35, true, 100.0, 10_000.0),
        };

        // Fully baffled condition: number of baffles x (baffle width / T) ~ 1/3.
        private const double FullyBaffledCoverage = 1.0 / 3.0;

        // Np fraction retained with no baffles (swirling / vortexing vessel).
        private const double UnbaffledPowerFactor = 0.30;

        // Shaft material allowable shear stress (mild carbon steel, conservative).
        private const double AllowableShearStressPa = 40_000_000;


        // Allowable shear = min(0.30 x Sy, 0.18 x Su) at temperature, x keyway reduction.
        private const double ShearFractionOfYield = 0.30;
        private const double ShearFractionOfUltimate = 0.18;
        private const double KeywayReductionFactor = 0.75;

        // ASME-style shock/fatigue factors for rotating shaft.
        private const double BendingFactorKm = 1.5;
        private const double TorsionFactorKt = 1.0;

        // Hydraulic radial force as a fraction of tangential force at 0.75 R.
        private const double RadialForceFraction = 0.5;

        // Motor sizing.
        private const double MotorServiceFactor = 1.25;
        private const double TransmissionEfficiency = 0.93; // gearbox + seal

        // Manual shaft physics (cantilever, Km = 1) defaults when the material has no value.
        private const double DefaultElasticModulusGpa = 200.0;
        private const double DefaultPoissonRatio = 0.30;
        private const double DefaultDensityKgM3 = 7850.0;
        private const double FallbackYieldMpa = 250.0;
        private const double FallbackUltimateMpa = 400.0;
        private const double EquivalentShaftMassFraction = 33.0 / 140.0;
        private const double ReliabilityFactor99 = 0.814;
        private const double EnduranceLimitCapPa = 700e6;
        private const double MaxReportedFactor = 999.0;

        // Surface finish constants (Sut in MPa): ka = a * Sut^b.
        private static readonly Dictionary<string, (double A, double B)> SurfaceFactors =
            new(StringComparer.OrdinalIgnoreCase)
            {
                ["ground"] = (1.58, -0.085),
                ["machined"] = (4.51, -0.265),
                ["cold_drawn"] = (4.51, -0.265),
                ["hot_rolled"] = (57.7, -0.718),
                ["as_forged"] = (272.0, -0.995),
            };

        private static readonly double[] StandardShaftSizesMm =
        {
            20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 90, 100,
            110, 120, 130, 140, 150, 160, 180, 200, 220, 240, 260, 280, 300
        };

        private readonly ShaftComparisonOptions _comparison;

        public AgitatorCalculationService(IOptions<ShaftComparisonOptions> comparison)
        {
            _comparison = comparison.Value;
        }

        public CalculationResult Calculate(Vessel vessel, Impeller impeller, FluidProperties fluid)
        {
            if (vessel is null) throw new ArgumentNullException(nameof(vessel));
            if (impeller is null) throw new ArgumentNullException(nameof(impeller));
            if (fluid is null) throw new ArgumentNullException(nameof(fluid));

            if (!Coefficients.TryGetValue(impeller.Type, out var data))
                throw new ArgumentOutOfRangeException(nameof(impeller), $"Unsupported impeller type {impeller.Type}.");

            if (impeller.RotationalSpeedRpm <= 0)
                throw new ArgumentOutOfRangeException(nameof(impeller), "Rotational speed must be greater than zero.");
            if (impeller.DiameterM <= 0)
                throw new ArgumentOutOfRangeException(nameof(impeller), "Impeller diameter must be greater than zero.");
            if (impeller.NumberOfImpellers < 1)
                throw new ArgumentOutOfRangeException(nameof(impeller), "At least one impeller is required.");
            if (vessel.DiameterM <= 0 || vessel.LiquidHeightM <= 0)
                throw new ArgumentOutOfRangeException(nameof(vessel), "Vessel dimensions must be greater than zero.");
            if (impeller.DiameterM >= vessel.DiameterM)
                throw new ArgumentOutOfRangeException(nameof(impeller), "Impeller diameter must be smaller than vessel diameter.");
            if (fluid.DensityKgM3 <= 0)
                throw new ArgumentOutOfRangeException(nameof(fluid), "Density must be greater than zero.");
            if (fluid.ViscosityPaS <= 0)
                throw new ArgumentOutOfRangeException(nameof(fluid), "Viscosity must be greater than zero.");
            if (impeller.ShaftMaterial is not null &&
    ((decimal)fluid.OperatingTemperatureC < impeller.ShaftMaterial.MinOperatingTempC ||
     (decimal)fluid.OperatingTemperatureC > impeller.ShaftMaterial.MaxOperatingTempC))
                throw new ArgumentOutOfRangeException(nameof(fluid),
                    $"Operating temperature {fluid.OperatingTemperatureC} °C is outside the valid range " +
                    $"({impeller.ShaftMaterial.MinOperatingTempC} to {impeller.ShaftMaterial.MaxOperatingTempC} °C) " +
                    $"for {impeller.ShaftMaterial.DisplayName}.");

            double n = impeller.RotationalSpeedRpm / 60.0; // rev/s
            double d = impeller.DiameterM;
            double rho = fluid.DensityKgM3;
            double mu = fluid.ViscosityPaS;

            double reynolds = rho * n * d * d / mu;
            var regime = ClassifyRegime(reynolds, data);

            double turbulentNp = TurbulentPowerNumber(data, vessel);
            double powerNumber = CalculatePowerNumber(reynolds, data, turbulentNp, regime);

            double singlePowerW = powerNumber * rho * Math.Pow(n, 3) * Math.Pow(d, 5);
            double effectiveCount = EffectiveImpellerCount(vessel, impeller);
            double powerWatts = singlePowerW * effectiveCount;

            double torqueNm = powerWatts / (2 * Math.PI * n);
            double tipSpeed = Math.PI * d * n;

            double allowableShearPa = ComputeAllowableShearStressPa(impeller.ShaftMaterial, fluid.OperatingTemperatureC);
            double shaftDiameterM = CalculateShaftDiameterM(vessel, impeller, torqueNm, allowableShearPa);
            double motorPowerKw = powerWatts * MotorServiceFactor / TransmissionEfficiency / 1000.0;

            double ratedTorqueNm = Math.Round(torqueNm, 2);
            var manual = ComputeManualPhysics(vessel, impeller, ratedTorqueNm, shaftDiameterM, fluid.OperatingTemperatureC);

            return new CalculationResult
            {
                ReynoldsNumber = Math.Round(reynolds, 1),
                Regime = regime,
                PowerNumber = Math.Round(powerNumber, 3),
                PowerDrawWatts = Math.Round(powerWatts, 1),
                TorqueNm = Math.Round(torqueNm, 2),
                TipSpeedMPerS = Math.Round(tipSpeed, 3),
                RecommendedShaftDiameterMm = Math.Round(shaftDiameterM * 1000, 1),
                RecommendedMotorPowerKw = Math.Round(motorPowerKw, 2),
                CalculatedAtUtc = DateTime.UtcNow,
                TipDeflectionMm = Math.Round(manual.TipDeflectionMm, 3),
                TwistDeg = Math.Round(manual.TwistDeg, 4),
                CriticalSpeedRpm = Math.Round(manual.CriticalSpeedRpm, 1),
                GoodmanFactor = Math.Round(manual.GoodmanFactor, 3)
            };
        }

        private static FlowRegime ClassifyRegime(double reynolds, ImpellerData data)
        {
            if (reynolds < data.LaminarReCutoff) return FlowRegime.Laminar;
            if (reynolds > data.TurbulentReCutoff) return FlowRegime.Turbulent;
            return FlowRegime.Transitional;
        }

        /// <summary>
        /// Fully turbulent Np. Close-clearance impellers (anchor, ribbon) are
        /// designed for unbaffled vessels, so no baffle penalty applies to them.
        /// For other impellers, Np scales between the unbaffled factor and 1.0
        /// depending on baffle coverage (count x width / T).
        /// </summary>
        private static double TurbulentPowerNumber(ImpellerData data, Vessel vessel)
        {
            if (data.CloseClearance)
                return data.Ct;

            double factor;
            if (!vessel.HasBaffles || vessel.NumberOfBaffles <= 0 || vessel.BaffleWidthToDiameterRatio <= 0)
            {
                factor = UnbaffledPowerFactor;
            }
            else
            {
                double coverage = vessel.NumberOfBaffles * vessel.BaffleWidthToDiameterRatio;
                double fraction = Math.Clamp(coverage / FullyBaffledCoverage, 0.0, 1.0);
                factor = UnbaffledPowerFactor + (1.0 - UnbaffledPowerFactor) * fraction;
            }

            return data.Ct * factor;
        }

        /// <summary>
        /// Laminar: Np = Kp / Re. Turbulent: Np = turbulent constant.
        /// Transitional: log-log interpolation between the laminar-end point
        /// and the turbulent plateau. Replace with digitized Np-vs-Re curve
        /// data for production accuracy.
        /// </summary>
        private static double CalculatePowerNumber(double reynolds, ImpellerData data,
            double turbulentNp, FlowRegime regime)
        {
            switch (regime)
            {
                case FlowRegime.Laminar:
                    return data.Kp / reynolds;

                case FlowRegime.Turbulent:
                    return turbulentNp;

                default:
                    double npAtLaminarEnd = data.Kp / data.LaminarReCutoff;
                    double logRe = Math.Log10(reynolds);
                    double logLow = Math.Log10(data.LaminarReCutoff);
                    double logHigh = Math.Log10(data.TurbulentReCutoff);
                    double t = (logRe - logLow) / (logHigh - logLow);
                    double logNp = Math.Log10(npAtLaminarEnd) +
                                   t * (Math.Log10(turbulentNp) - Math.Log10(npAtLaminarEnd));
                    return Math.Pow(10, logNp);
            }
        }

        /// <summary>
        /// Effective number of impellers for power. The first impeller counts
        /// fully; each additional one counts by a spacing-based factor
        /// (closely spaced impellers interact and draw less than a full unit).
        /// Spacing is estimated from the liquid height above the clearance.
        /// </summary>
        private static double EffectiveImpellerCount(Vessel vessel, Impeller impeller)
        {
            int count = impeller.NumberOfImpellers;
            if (count <= 1) return 1.0;

            double clearance = impeller.ClearanceToDiameterRatio * vessel.DiameterM;
            double available = Math.Max(vessel.LiquidHeightM - clearance, impeller.DiameterM);
            double spacing = available / count;
            double spacingRatio = spacing / impeller.DiameterM;

            double additionalFactor = Math.Clamp(0.6 + 0.4 * spacingRatio, 0.7, 1.0);
            return 1.0 + (count - 1) * additionalFactor;
        }

        /// <summary>
        /// Shaft diameter from combined torsion + bending (ASME-style):
        /// d = [ 16 / (pi * tau) * sqrt((Km*M)^2 + (Kt*T)^2) ]^(1/3).
        /// T = design torque (rated torque x service factor).
        /// M = bending moment from hydraulic radial force over shaft length.
        /// Result is rounded up to the next standard shaft size.
        /// </summary>
        private static double CalculateShaftDiameterM(Vessel vessel, Impeller impeller, double ratedTorqueNm, double allowableShearPa)
        {
            double designTorque = ratedTorqueNm * MotorServiceFactor;

            double effectiveRadius = 0.75 * impeller.DiameterM / 2.0;
            double tangentialForce = designTorque / effectiveRadius;
            double radialForce = RadialForceFraction * tangentialForce;

            double shaftLengthM = vessel.LiquidHeightM * 1.15;
            double bendingMoment = radialForce * shaftLengthM;

            double equivalentTorque = Math.Sqrt(
                Math.Pow(BendingFactorKm * bendingMoment, 2) +
                Math.Pow(TorsionFactorKt * designTorque, 2));

            double diameterM = Math.Cbrt(16.0 * equivalentTorque / (Math.PI * allowableShearPa));
            return RoundUpToStandardSizeM(diameterM);
        }

        /// <summary>Design torque (N*m) = rated torque x motor service factor.</summary>
        public static double ComputeDesignTorqueNm(double ratedTorqueNm)
        {
            return ratedTorqueNm * MotorServiceFactor;
        }

        /// <summary>Hydraulic radial force (N) at the impeller, same basis as the shaft formula.</summary>
        public static double ComputeRadialForceN(double ratedTorqueNm, double impellerDiameterM)
        {
            double effectiveRadius = 0.75 * impellerDiameterM / 2.0;
            double tangentialForce = ComputeDesignTorqueNm(ratedTorqueNm) / effectiveRadius;
            return RadialForceFraction * tangentialForce;
        }

        /// <summary>Shaft length (m) used by the shaft formula.</summary>
        public static double ComputeShaftLengthM(double liquidHeightM)
        {
            return liquidHeightM * 1.15;
        }
        /// <summary>
        /// Allowable shear stress (Pa) for the shaft material at the operating temperature.
        /// Falls back to the fixed constant when no material is selected.
        /// </summary>
        public static double ComputeAllowableShearStressPa(ShaftMaterial? material, double temperatureC)
        {
            if (material is null) return AllowableShearStressPa;

            double factor = GetStrengthFactor(material, temperatureC);
            double yieldPa = (double)material.YieldStrengthMpa * 1_000_000.0 * factor;
            double ultimatePa = (double)material.UltimateStrengthMpa * 1_000_000.0 * factor;

            double allowable = Math.Min(ShearFractionOfYield * yieldPa, ShearFractionOfUltimate * ultimatePa);
            return allowable * KeywayReductionFactor;
        }

        /// <summary>Yield strength (MPa) at the operating temperature.</summary>
        public static double ComputeYieldAtTemperatureMpa(ShaftMaterial material, double temperatureC)
        {
            return (double)material.YieldStrengthMpa * GetStrengthFactor(material, temperatureC);
        }

        /// <summary>Linear interpolation of the strength factor from the derating points.</summary>
        private static double GetStrengthFactor(ShaftMaterial material, double temperatureC)
        {
            var points = material.TempDeratingPoints
                .OrderBy(p => p.TemperatureC)
                .ToList();

            if (points.Count == 0) return 1.0;

            double first = (double)points[0].TemperatureC;
            double last = (double)points[^1].TemperatureC;

            if (temperatureC <= first) return (double)points[0].StrengthFactor;
            if (temperatureC >= last) return (double)points[^1].StrengthFactor;

            for (int i = 0; i < points.Count - 1; i++)
            {
                double t1 = (double)points[i].TemperatureC;
                double t2 = (double)points[i + 1].TemperatureC;
                if (temperatureC >= t1 && temperatureC <= t2)
                {
                    double f1 = (double)points[i].StrengthFactor;
                    double f2 = (double)points[i + 1].StrengthFactor;
                    return f1 + (f2 - f1) * (temperatureC - t1) / (t2 - t1);
                }
            }

            return 1.0;
        }

        private static double RoundUpToStandardSizeM(double diameterM)
        {
            double mm = diameterM * 1000.0;

            foreach (double size in StandardShaftSizesMm)
            {
                if (mm <= size) return size / 1000.0;
            }

            return Math.Ceiling(mm / 10.0) * 10.0 / 1000.0;
        }

        // ------------------------------------------------------------------
        // Manual shaft physics (cantilever, Km = 1) and the three-way comparison
        // ------------------------------------------------------------------
        private readonly record struct ManualShaftPhysics(
            double RootMomentNm,
            double TipDeflectionMm,
            double TwistDeg,
            double MaxShearMpa,
            double VonMisesMpa,
            double CriticalSpeedRpm,
            double GoodmanFactor,
            double SoderbergFactor,
            double YieldFactor);

        private static ManualShaftPhysics ComputeManualPhysics(
            Vessel vessel,
            Impeller impeller,
            double ratedTorqueNm,
            double diameterM,
            double temperatureC)
        {
            var material = impeller.ShaftMaterial;

            double lengthM = ComputeShaftLengthM(vessel.LiquidHeightM);
            double radialForceN = ComputeRadialForceN(ratedTorqueNm, impeller.DiameterM);
            double torqueNm = ComputeDesignTorqueNm(ratedTorqueNm);
            double momentNm = radialForceN * lengthM;

            double ePa = ((double?)material?.ElasticModulusGpa ?? DefaultElasticModulusGpa) * 1e9;
            double nu = (double?)material?.PoissonRatio ?? DefaultPoissonRatio;
            double density = (double?)material?.DensityKgM3 ?? DefaultDensityKgM3;
            double gPa = ePa / (2.0 * (1.0 + nu));

            double yieldPa = (material is null
                ? FallbackYieldMpa
                : ComputeYieldAtTemperatureMpa(material, temperatureC)) * 1e6;
            double ultimatePa = ((double?)material?.UltimateStrengthMpa ?? FallbackUltimateMpa) * 1e6;

            double iM4 = Math.PI * Math.Pow(diameterM, 4) / 64.0;
            double jM4 = 2.0 * iM4;
            double areaM2 = Math.PI * diameterM * diameterM / 4.0;
            double d3 = Math.PI * Math.Pow(diameterM, 3);

            double tipDeflectionM = radialForceN * Math.Pow(lengthM, 3) / (3.0 * ePa * iM4);
            double twistRad = torqueNm * lengthM / (gPa * jM4);

            double sigmaB = 32.0 * momentNm / d3;
            double tauT = 16.0 * torqueNm / d3;
            double maxShearPa = 16.0 * Math.Sqrt(momentNm * momentNm + torqueNm * torqueNm) / d3;
            double vonMisesPa = Math.Sqrt(sigmaB * sigmaB + 3.0 * tauT * tauT);

            double impellerMassKg = (double?)impeller.ImpellerMassKg ?? 0.0;
            double shaftMassKg = density * areaM2 * lengthM;
            double equivalentMassKg = impellerMassKg + EquivalentShaftMassFraction * shaftMassKg;
            double omega = Math.Sqrt(3.0 * ePa * iM4 / (equivalentMassKg * Math.Pow(lengthM, 3)));
            double criticalRpm = omega * 60.0 / (2.0 * Math.PI);

            double sePa = CorrectedEnduranceLimitPa(material, ultimatePa, diameterM);
            double sigmaA = sigmaB;
            double sigmaM = Math.Sqrt(3.0) * tauT;

            return new ManualShaftPhysics(
                RootMomentNm: momentNm,
                TipDeflectionMm: tipDeflectionM * 1000.0,
                TwistDeg: twistRad * 180.0 / Math.PI,
                MaxShearMpa: maxShearPa / 1e6,
                VonMisesMpa: vonMisesPa / 1e6,
                CriticalSpeedRpm: criticalRpm,
                GoodmanFactor: SafetyFactor(sigmaA / sePa + sigmaM / ultimatePa),
                SoderbergFactor: SafetyFactor(sigmaA / sePa + sigmaM / yieldPa),
                YieldFactor: SafetyFactor((sigmaA + sigmaM) / yieldPa));
        }

        private static double SafetyFactor(double denominator)
        {
            return denominator <= 0.0 ? MaxReportedFactor : Math.Min(1.0 / denominator, MaxReportedFactor);
        }

        private static double CorrectedEnduranceLimitPa(ShaftMaterial? material, double ultimatePa, double diameterM)
        {
            double? measuredMpa = (double?)material?.EnduranceLimitMpa;
            double sePrime = measuredMpa.HasValue
                ? measuredMpa.Value * 1e6
                : Math.Min(0.5 * ultimatePa, EnduranceLimitCapPa);

            string finish = material?.SurfaceFinish ?? "machined";
            if (!SurfaceFactors.TryGetValue(finish, out var surface))
            {
                surface = SurfaceFactors["machined"];
            }

            double ka = surface.A * Math.Pow(ultimatePa / 1e6, surface.B);

            double dMm = diameterM * 1000.0;
            double kb;
            if (dMm < 2.79) kb = 1.0;
            else if (dMm <= 51.0) kb = 1.24 * Math.Pow(dMm, -0.107);
            else if (dMm <= 254.0) kb = 1.51 * Math.Pow(dMm, -0.157);
            else kb = 0.60;

            return ka * kb * ReliabilityFactor99 * sePrime;
        }

        private static ShaftFeaResult? ReadFea(string? json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return null;
            }

            try
            {
                var fea = JsonSerializer.Deserialize<ShaftFeaResult>(json);
                return fea is not null && fea.IsOk ? fea : null;
            }
            catch (JsonException)
            {
                return null;
            }
        }

        public ShaftComparisonResult BuildShaftComparison(
            Vessel vessel,
            Impeller impeller,
            FluidProperties fluid,
            CalculationResult calculation,
            ShaftPredictionResult? prediction)
        {
            if (vessel is null) throw new ArgumentNullException(nameof(vessel));
            if (impeller is null) throw new ArgumentNullException(nameof(impeller));
            if (fluid is null) throw new ArgumentNullException(nameof(fluid));
            if (calculation is null) throw new ArgumentNullException(nameof(calculation));

            double diameterM = calculation.RecommendedShaftDiameterMm / 1000.0;
            var manual = ComputeManualPhysics(vessel, impeller, calculation.TorqueNm, diameterM, fluid.OperatingTemperatureC);

            var fea = ReadFea(calculation.FeaCheckJson);
            var pinn = prediction?.Physics;
            bool pinnTrusted = prediction is not null && pinn is not null && prediction.IsTrusted;

            double allowableShearMpa =
                ComputeAllowableShearStressPa(impeller.ShaftMaterial, fluid.OperatingTemperatureC) / 1e6;
            double shaftLengthM = ComputeShaftLengthM(vessel.LiquidHeightM);

            double? feaDiameterMm = null;
            if (fea is not null && fea.MaxShearMpa is double feaShear && feaShear > 0 && allowableShearMpa > 0)
            {
                feaDiameterMm = calculation.RecommendedShaftDiameterMm * Math.Cbrt(feaShear / allowableShearMpa);
            }

            var result = new ShaftComparisonResult
            {
                StandardShaftSizeMm = calculation.RecommendedShaftDiameterMm,
                FeaAvailable = fea is not null,
                ModelVersion = prediction?.ModelVersion ?? string.Empty,
                PdeResidual = pinn?.PdeResidual ?? 0.0,
                GeneratedAtUtc = DateTime.UtcNow
            };

            // ---- Side-by-side quantities: manual, FEA, PINN ----
            result.Quantities.Add(new ShaftQuantityComparison
            {
                Name = "Shaft diameter (before rounding)",
                Unit = "mm",
                Manual = prediction is null ? calculation.RecommendedShaftDiameterMm : prediction.RawFormulaDiameterMm,
                Fea = feaDiameterMm,
                Pinn = prediction?.RawPredictedDiameterMm,
                TolerancePercent = _comparison.DiameterTolerancePercent
            });
            result.Quantities.Add(new ShaftQuantityComparison
            {
                Name = "Tip deflection",
                Unit = "mm",
                Manual = manual.TipDeflectionMm,
                Fea = fea?.TipDeflectionMm,
                Pinn = pinn?.TipDeflectionMm,
                TolerancePercent = _comparison.DeflectionTolerancePercent
            });
            result.Quantities.Add(new ShaftQuantityComparison
            {
                Name = "Angle of twist",
                Unit = "deg",
                Manual = manual.TwistDeg,
                Fea = fea?.TwistDeg,
                Pinn = pinn?.TwistDeg,
                TolerancePercent = _comparison.TwistTolerancePercent
            });
            result.Quantities.Add(new ShaftQuantityComparison
            {
                Name = "Max shear stress",
                Unit = "MPa",
                Manual = manual.MaxShearMpa,
                Fea = fea?.MaxShearMpa,
                Pinn = pinn?.MaxShearStressMpa,
                TolerancePercent = _comparison.DiameterTolerancePercent
            });
            result.Quantities.Add(new ShaftQuantityComparison
            {
                Name = "Von Mises stress",
                Unit = "MPa",
                Manual = manual.VonMisesMpa,
                Fea = fea?.VonMisesMpa,
                Pinn = pinn?.VonMisesStressMpa,
                TolerancePercent = _comparison.DiameterTolerancePercent
            });

            // ---- Design checks: FEA first, then trusted PINN, then manual ----
            (double Value, string Source) Pick(double? feaValue, double? pinnValue, double manualValue)
            {
                if (feaValue.HasValue) return (feaValue.Value, "FEA");
                if (pinnTrusted && pinnValue.HasValue) return (pinnValue.Value, "PINN");
                return (manualValue, "Manual");
            }

            var strength = Pick(fea?.MaxShearMpa, pinn?.MaxShearStressMpa, manual.MaxShearMpa);
            result.Checks.Add(new ShaftCheckResult
            {
                Kind = ShaftCheckKind.Strength,
                Name = "Strength (max shear stress)",
                Unit = "MPa",
                DemandValue = strength.Value,
                CapacityValue = allowableShearMpa,
                Source = strength.Source
            });

            var deflection = Pick(fea?.TipDeflectionMm, pinn?.TipDeflectionMm, manual.TipDeflectionMm);
            result.Checks.Add(new ShaftCheckResult
            {
                Kind = ShaftCheckKind.Deflection,
                Name = "Tip deflection",
                Unit = "mm",
                DemandValue = deflection.Value,
                CapacityValue = _comparison.DeflectionLimitRatio * shaftLengthM * 1000.0,
                Source = deflection.Source
            });

            var critical = Pick(null, pinn?.CriticalSpeedRpm, manual.CriticalSpeedRpm);
            result.Checks.Add(new ShaftCheckResult
            {
                Kind = ShaftCheckKind.CriticalSpeed,
                Name = "Operating speed vs critical speed",
                Unit = "rpm",
                DemandValue = impeller.RotationalSpeedRpm,
                CapacityValue = _comparison.MaxCriticalSpeedRatio * critical.Value,
                Source = critical.Source
            });

            var goodman = Pick(null, pinn?.GoodmanFactor, manual.GoodmanFactor);
            result.Checks.Add(new ShaftCheckResult
            {
                Kind = ShaftCheckKind.Fatigue,
                Name = "Fatigue (Goodman factor)",
                Unit = "factor",
                DemandValue = _comparison.RequiredFatigueFactor,
                CapacityValue = Math.Min(goodman.Value, MaxReportedFactor),
                Source = goodman.Source
            });

            // ---- Notes ----
            if (fea is null)
            {
                result.Notes.Add("FEA result not available; checks use PINN (when trusted) or manual values.");
            }

            if (prediction is null)
            {
                result.Notes.Add("PINN prediction not available.");
            }
            else if (!prediction.IsTrusted)
            {
                result.Notes.Add("PINN result is not trusted; checks use FEA or manual values.");
            }

            if (prediction is not null && !string.IsNullOrWhiteSpace(prediction.Notes))
            {
                result.Notes.Add(prediction.Notes);
            }

            return result;
        }
    }
}