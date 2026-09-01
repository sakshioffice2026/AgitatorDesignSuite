using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace Agitator.Core.Entities
{
    public enum VesselHeadType
    {
        Flat,
        Dished,
        Conical,
        Hemispherical
    }

    /// <summary>
    /// Process tank geometry. All dimensions in SI units (metres) to keep the
    /// calculation engine unit-consistent; convert at the UI edge only.
    /// </summary>
    public class Vessel
    {
        public int Id { get; set; }

        [ForeignKey(nameof(Project))]
        public int ProjectId { get; set; }
        public Project? Project { get; set; }

        [Range(0.1, 50)]
        public double DiameterM { get; set; }

        [Range(0.1, 50)]
        public double LiquidHeightM { get; set; }

        public VesselHeadType HeadType { get; set; } = VesselHeadType.Dished;

        public bool HasBaffles { get; set; } = true;

        [Range(0, 8)]
        public int NumberOfBaffles { get; set; } = 4;

        /// <summary>Baffle width as a fraction of vessel diameter, typically ~1/12.</summary>
        [Range(0, 1)]
        public double BaffleWidthToDiameterRatio { get; set; } = 0.0833;
    }
}