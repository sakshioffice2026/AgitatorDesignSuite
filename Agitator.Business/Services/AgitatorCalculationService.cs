using Agitator.Business.Contracts;
using Agitator.Core.Entities;

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

        private static readonly double[] StandardShaftSizesMm =
        {
            20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 90, 100,
            110, 120, 130, 140, 150, 160, 180, 200, 220, 240, 260, 280, 300
        };

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
                CalculatedAtUtc = DateTime.UtcNow
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
    }
}