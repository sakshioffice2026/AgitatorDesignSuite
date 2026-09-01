using Agitator.Business.Contracts;
using Agitator.Core.Entities;

namespace Agitator.Business
{
    /// <summary>
    /// Core process + mechanical calculation engine.
    ///
    /// IMPORTANT: The Kp/Ct coefficients in ImpellerCoefficients are
    /// representative textbook values for illustration only. Before using
    /// this for real equipment sizing, replace them with coefficients
    /// validated against your own test data, vendor curves, or a licensed
    /// correlation source (e.g. published Np-vs-Re curves per impeller
    /// geometry). This is the module to treat as proprietary IP — keep the
    /// coefficient source data server-side only, never in client-side code.
    /// </summary>
    public class AgitatorCalculationService : IAgitatorCalculationService
    {
        private static readonly Dictionary<ImpellerType, (double Kp, double Ct)> ImpellerCoefficients = new()
        {
            [ImpellerType.RushtonTurbine] = (71.0, 5.00),
            [ImpellerType.PitchedBladeTurbine] = (70.0, 1.27),
            [ImpellerType.Propeller] = (41.0, 0.32),
            [ImpellerType.HydrofoilA310] = (65.0, 0.30),
            [ImpellerType.AnchorFoil] = (420.0, 0.35),
            [ImpellerType.HelicalRibbon] = (980.0, 0.20),
        };

        // Reynolds number thresholds for flow regime classification (Oldshue).
        private const double LaminarReCutoff = 10.0;
        private const double TurbulentReCutoff = 10000.0;

        // Allowable shear stress for shaft material (default: mild carbon steel,
        // conservative design allowable). Override per-project once you add
        // material selection to the UI.
        private const double AllowableShearStressPa = 40_000_000; // 40 MPa

        // Motor sizing service factor to cover startup torque / process upsets.
        private const double MotorServiceFactor = 1.25;

        public CalculationResult Calculate(Vessel vessel, Impeller impeller, FluidProperties fluid)
        {
            if (vessel is null) throw new ArgumentNullException(nameof(vessel));
            if (impeller is null) throw new ArgumentNullException(nameof(impeller));
            if (fluid is null) throw new ArgumentNullException(nameof(fluid));

            double n = impeller.RotationalSpeedRpm / 60.0; // rev/s
            double d = impeller.DiameterM;
            double rho = fluid.DensityKgM3;
            double mu = fluid.ViscosityPaS;

            double reynolds = (rho * n * d * d) / mu;
            var regime = ClassifyRegime(reynolds);

            var (kp, ct) = ImpellerCoefficients[impeller.Type];
            double powerNumber = CalculatePowerNumber(reynolds, kp, ct, regime, vessel.HasBaffles);

            // P = Np * rho * N^3 * D^5, multiplied by number of impellers on
            // the shaft (a simplification — true multi-impeller interaction
            // depends on spacing; refine with a spacing-based correction
            // factor if you need higher accuracy).
            double powerWatts = powerNumber * rho * Math.Pow(n, 3) * Math.Pow(d, 5)
                                 * impeller.NumberOfImpellers;

            double torqueNm = n > 0 ? powerWatts / (2 * Math.PI * n) : 0;
            double tipSpeed = Math.PI * d * n;

            double shaftDiameterM = Math.Pow(
                (16.0 * torqueNm) / (Math.PI * AllowableShearStressPa),
                1.0 / 3.0);

            double motorPowerKw = (powerWatts * MotorServiceFactor) / 1000.0;

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

        private static FlowRegime ClassifyRegime(double reynolds)
        {
            if (reynolds < LaminarReCutoff) return FlowRegime.Laminar;
            if (reynolds > TurbulentReCutoff) return FlowRegime.Turbulent;
            return FlowRegime.Transitional;
        }

        /// <summary>
        /// Np in the laminar region follows Np = Kp / Re. In the fully
        /// turbulent region Np approaches a constant Ct (for baffled
        /// vessels). Between the two, we log-interpolate as a pragmatic
        /// stand-in for a true digitized Np-vs-Re curve — replace with
        /// interpolation against real curve data (e.g. via MathNet.Numerics
        /// cubic splines) for production accuracy.
        /// </summary>
        private static double CalculatePowerNumber(double reynolds, double kp, double ct,
            FlowRegime regime, bool baffled)
        {
            double turbulentNp = baffled ? ct : ct * 0.25; // unbaffled vessels swirl,
                                                           // losing effective power draw

            switch (regime)
            {
                case FlowRegime.Laminar:
                    return kp / Math.Max(reynolds, 0.01);

                case FlowRegime.Turbulent:
                    return turbulentNp;

                default:
                    double npAtLaminarEnd = kp / LaminarReCutoff;
                    double logRe = Math.Log10(reynolds);
                    double logLow = Math.Log10(LaminarReCutoff);
                    double logHigh = Math.Log10(TurbulentReCutoff);
                    double t = (logRe - logLow) / (logHigh - logLow);
                    double logNp = Math.Log10(npAtLaminarEnd) +
                                   t * (Math.Log10(turbulentNp) - Math.Log10(npAtLaminarEnd));
                    return Math.Pow(10, logNp);
            }
        }
    }
}