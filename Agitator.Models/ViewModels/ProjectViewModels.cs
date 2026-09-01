using System.ComponentModel.DataAnnotations;
using Agitator.Core.Entities;

namespace Agitator.Models.ViewModel
{
    // ============================================================
    // INDEX — list row shown on /Projects
    // ============================================================
    public class ProjectListItemViewModel
    {
        public int Id { get; set; }
        public string Name { get; set; } = string.Empty;
        public string? Description { get; set; }
        public DateTime CreatedAt { get; set; }
        public CadJobStatus CadStatus { get; set; }
    }

    // ============================================================
    // FORM — backs the Create/Edit form
    // ============================================================
    public class ProjectFormViewModel
    {
        public int Id { get; set; }

        [Required, StringLength(150)]
        [Display(Name = "Project name")]
        public string Name { get; set; } = string.Empty;

        [StringLength(500)]
        public string? Description { get; set; }

        // Vessel
        [Range(0.1, 50)]
        [Display(Name = "Vessel diameter (m)")]
        public double VesselDiameterM { get; set; } = 2.0;

        [Range(0.1, 50)]
        [Display(Name = "Liquid height (m)")]
        public double LiquidHeightM { get; set; } = 2.0;

        [Display(Name = "Baffled vessel?")]
        public bool HasBaffles { get; set; } = true;

        [Range(0, 8)]
        [Display(Name = "Number of baffles")]
        public int NumberOfBaffles { get; set; } = 4;

        // Impeller
        [Display(Name = "Impeller type")]
        public ImpellerType ImpellerType { get; set; } = ImpellerType.RushtonTurbine;

        [Range(0.01, 20)]
        [Display(Name = "Impeller diameter (m)")]
        public double ImpellerDiameterM { get; set; } = 0.7;

        [Range(0.01, 3000)]
        [Display(Name = "Rotational speed (RPM)")]
        public double RotationalSpeedRpm { get; set; } = 90;

        [Range(1, 5)]
        [Display(Name = "Number of impellers")]
        public int NumberOfImpellers { get; set; } = 1;

        // Fluid
        [Range(1, 5000)]
        [Display(Name = "Fluid density (kg/m3)")]
        public double DensityKgM3 { get; set; } = 1000;

        [Range(0.0001, 1000)]
        [Display(Name = "Viscosity (Pa.s)")]
        public double ViscosityPaS { get; set; } = 0.001;
    }

    // ============================================================
    // DETAIL — backs /Projects/Detail and /Projects/Results
    // ============================================================
    public class ProjectDetailViewModel
    {
        public int Id { get; set; }
        public string Name { get; set; } = string.Empty;
        public string? Description { get; set; }
        public DateTime CreatedAt { get; set; }
        public DateTime? UpdatedAt { get; set; }

        public Vessel? Vessel { get; set; }
        public Impeller? Impeller { get; set; }
        public FluidProperties? FluidProperties { get; set; }
        public CalculationResult? CalculationResult { get; set; }
    }
}