using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace Agitator.Core.Entities
{
    /// <summary>
    /// Impeller types with published power-number correlations.
    /// Kp = laminar power constant, Ct = turbulent (fully baffled) power number.
    /// Values are representative textbook figures (Oldshue; Paul, Atiemo-Obeng &amp;
    /// Kresta, "Handbook of Industrial Mixing") — replace with your own validated
    /// correlations/curve data before relying on this for real equipment sizing.
    /// The values actually used live in AgitatorCalculationService.
    /// </summary>
    public enum ImpellerType
    {
        RushtonTurbine,      // Kp=70,  Ct=5.00 (6-blade disc turbine, baffled)
        PitchedBladeTurbine, // Kp=70,  Ct=1.27 (4-blade, 45°, baffled)
        Propeller,           // Kp=41,  Ct=0.32 (square pitch, baffled)
        HydrofoilA310,       // Kp=65,  Ct=0.30 (baffled)
        AnchorFoil,          // Kp=300, Ct=0.35 (close-clearance, unbaffled, laminar to Re~100)
        HelicalRibbon        // Kp=300, Ct=0.35 (close-clearance, unbaffled, laminar to Re~100)
    }

    public class Impeller
    {
        public int Id { get; set; }

        [ForeignKey(nameof(Project))]
        public int ProjectId { get; set; }
        public Project? Project { get; set; }

        public ImpellerType Type { get; set; } = ImpellerType.RushtonTurbine;

        [Range(0.01, 20)]
        public double DiameterM { get; set; }

        [Range(0.01, 3000)]
        public double RotationalSpeedRpm { get; set; }

        [Range(1, 5)]
        public int NumberOfImpellers { get; set; } = 1;

        /// <summary>
        /// Off-bottom clearance C/T: clearance as a fraction of VESSEL diameter, typically ~1/3.
        /// Used to estimate impeller spacing for multi-impeller power.
        /// </summary>
        [Range(0, 1)]
        public double ClearanceToDiameterRatio { get; set; } = 0.33;

        /// <summary>
        /// Total mass (kg) of all impellers and hubs carried by the shaft.
        /// Used for the critical speed calculation.
        /// </summary>
        [Range(0, 50000)]
        public double ImpellerMassKg { get; set; }


        /// <summary>Shaft material and heat treatment (FK to ShaftMaterials).</summary>
        public int? ShaftMaterialId { get; set; }
        public ShaftMaterial? ShaftMaterial { get; set; }
    }
}