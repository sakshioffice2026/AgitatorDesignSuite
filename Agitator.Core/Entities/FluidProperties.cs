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

        /// <summary>
        /// Rheology flag. The calculation engine treats every fluid as Newtonian, so a value of false
        /// is not corrected for shear-thinning or shear-thickening (no Metzner-Otto shear-rate viscosity).
        /// Power, Reynolds number and regime are not valid for non-Newtonian fluids.
        /// </summary>
        public bool IsNewtonian { get; set; } = true;


        /// <summary>Operating temperature of the fluid and shaft in °C.</summary>
        [Range(-200, 600)]
        public double OperatingTemperatureC { get; set; } = 30;

        [StringLength(200)]
        public string? Notes { get; set; }
    }
}