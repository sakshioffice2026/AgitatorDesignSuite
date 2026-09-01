using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace Agitator.Core.Entities
{
    public class FluidProperties
    {
        public int Id { get; set; }

        [ForeignKey(nameof(Project))]
        public int ProjectId { get; set; }
        public Project? Project { get; set; }

        [Range(1, 5000)]
        public double DensityKgM3 { get; set; } = 1000;

        /// <summary>Dynamic viscosity in Pa·s (Newtonian assumption for v1).</summary>
        [Range(0.0001, 1000)]
        public double ViscosityPaS { get; set; } = 0.001;

        public bool IsNewtonian { get; set; } = true;

        [StringLength(200)]
        public string? Notes { get; set; }
    }
}