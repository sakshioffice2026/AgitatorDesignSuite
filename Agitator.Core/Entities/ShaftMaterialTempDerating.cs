using System.ComponentModel.DataAnnotations.Schema;

namespace Agitator.Core.Entities
{
    /// <summary>
    /// Strength factor (relative to room temperature) of a shaft material at a given temperature.
    /// Maps to table ShaftMaterialTempDerating (database script 2).
    /// </summary>
    public class ShaftMaterialTempDerating
    {
        public int Id { get; set; }

        [ForeignKey(nameof(ShaftMaterial))]
        public int ShaftMaterialId { get; set; }
        public ShaftMaterial? ShaftMaterial { get; set; }

        /// <summary>Temperature point (°C).</summary>
        [Column(TypeName = "decimal(6,1)")]
        public decimal TemperatureC { get; set; }

        /// <summary>Multiplier applied to room temperature yield AND ultimate strength (1.0 = no reduction). Elastic modulus is not derated.</summary>
        [Column(TypeName = "decimal(5,4)")]
        public decimal StrengthFactor { get; set; }
    }
}
