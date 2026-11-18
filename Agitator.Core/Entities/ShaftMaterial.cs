using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace Agitator.Core.Entities
{
    /// <summary>
    /// Shaft material grade with its heat treatment condition.
    /// Maps to table ShaftMaterials (database script 1).
    /// </summary>
    public class ShaftMaterial
    {
        public int Id { get; set; }

        [Required, StringLength(50)]
        public string Grade { get; set; } = string.Empty;

        [Required, StringLength(80)]
        public string HeatTreatment { get; set; } = string.Empty;

        /// <summary>Yield strength at room temperature (MPa).</summary>
        [Column(TypeName = "decimal(8,2)")]
        public decimal YieldStrengthMpa { get; set; }

        /// <summary>Ultimate tensile strength at room temperature (MPa).</summary>
        [Column(TypeName = "decimal(8,2)")]
        public decimal UltimateStrengthMpa { get; set; }

        /// <summary>Lowest permitted operating temperature (°C).</summary>
        [Column(TypeName = "decimal(6,1)")]
        public decimal MinOperatingTempC { get; set; }

        /// <summary>Highest permitted operating temperature (°C).</summary>
        [Column(TypeName = "decimal(6,1)")]
        public decimal MaxOperatingTempC { get; set; }

        /// <summary>Elastic modulus at room temperature (GPa). Not temperature-derated: deflection and critical speed are unconservative at high temperature (modulus drops roughly 5% at 300 °C for carbon steel).</summary>
        [Column(TypeName = "decimal(7,2)")]
        public decimal? ElasticModulusGpa { get; set; }

        /// <summary>Poisson's ratio (dimensionless).</summary>
        [Column(TypeName = "decimal(5,3)")]
        public decimal? PoissonRatio { get; set; }

        /// <summary>Density (kg/m3).</summary>
        [Column(TypeName = "decimal(8,2)")]
        public decimal? DensityKgM3 { get; set; }

        /// <summary>Measured rotating-beam endurance limit Se' at room temperature (MPa), before Marin factors. Null means 0.5 x ultimate strength, capped at 700 MPa. Not temperature-derated in the database; the engine derates it with the strength factor.</summary>
        [Column(TypeName = "decimal(8,2)")]
        public decimal? EnduranceLimitMpa { get; set; }

        /// <summary>Surface finish for the fatigue surface factor: ground, machined, cold_drawn, hot_rolled or as_forged.</summary>
        [StringLength(20)]
        public string? SurfaceFinish { get; set; }

        public bool IsActive { get; set; } = true;

        public ICollection<ShaftMaterialTempDerating> TempDeratingPoints { get; set; }
            = new List<ShaftMaterialTempDerating>();

        [NotMapped]
        public string DisplayName => $"{Grade} - {HeatTreatment}";
    }
}