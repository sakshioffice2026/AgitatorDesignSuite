using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace Agitator.Core.Entities
{
    /// <summary>
    /// Impeller types with published power-number correlations.
    /// Kp = laminar power constant, Ct = turbulent (fully baffled) power number.
    /// Values are representative textbook figures (Oldshue; Paul, Atiemo-Obeng &
    /// Kresta, "Handbook of Industrial Mixing") — replace with your own validated
    /// correlations/curve data before relying on this for real equipment sizing.
    /// </summary>
    public enum ImpellerType
    {
        RushtonTurbine,      // Kp=71,  Ct=5.0
        PitchedBladeTurbine, // Kp=70,  Ct=1.27 (4-blade, 45°)
        Propeller,           // Kp=41,  Ct=0.32
        HydrofoilA310,       // Kp=65,  Ct=0.30
        AnchorFoil,          // Kp=420, Ct=0.35 (no baffles, close-clearance)
        HelicalRibbon        // Kp=980, Ct=0.20 (no baffles, close-clearance)
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

        /// <summary>Off-bottom clearance as a fraction of vessel diameter, typically ~1/3.</summary>
        [Range(0, 2)]
        public double ClearanceToDiameterRatio { get; set; } = 0.33;
    }
}