using System.ComponentModel.DataAnnotations;

namespace Agitator.Core.Entities
{
    /// <summary>
    /// Top-level container for a customer's agitator design job.
    /// One Project has one Vessel, one Impeller configuration, one FluidProperties
    /// record, and (after calculation) one CalculationResult. CAD/report jobs
    /// hang off the CalculationResult.
    /// </summary>
    public class Project
    {
        public int Id { get; set; }

        [Required, StringLength(150)]
        public string Name { get; set; } = string.Empty;

        [StringLength(500)]
        public string? Description { get; set; }

        // Multi-tenancy: every row scoped to a customer account.
        [Required]
        public string TenantId { get; set; } = string.Empty;

        // --- Audit fields ---
        public string? CreatedByUserId { get; set; }
        public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
        public DateTime? UpdatedAt { get; set; }

        public Vessel? Vessel { get; set; }
        public Impeller? Impeller { get; set; }
        public FluidProperties? FluidProperties { get; set; }
        public CalculationResult? CalculationResult { get; set; }
    }
}