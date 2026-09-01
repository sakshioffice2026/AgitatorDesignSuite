using Agitator.Core;
using Agitator.Core.Entities;
using Agitator.Models.ViewModel;
using Agitator.Repositories.Contracts;
using Microsoft.EntityFrameworkCore;

namespace Agitator.Repositories.Repositories
{
    public class ProjectRepository : IProjectRepository
    {
        private readonly ApplicationDbContext _db;

        public ProjectRepository(ApplicationDbContext db)
        {
            _db = db;
        }

        public async Task<List<ProjectListItemViewModel>> GetProjectIndexDataAsync(string tenantId)
        {
            return await _db.Projects
                .Where(p => p.TenantId == tenantId)
                .OrderByDescending(p => p.CreatedAt)
                .Take(25)
                .Select(p => new ProjectListItemViewModel
                {
                    Id = p.Id,
                    Name = p.Name,
                    Description = p.Description,
                    CreatedAt = p.CreatedAt,
                    CadStatus = p.CalculationResult != null ? p.CalculationResult.CadStatus : CadJobStatus.NotRequested
                })
                .ToListAsync();
        }

        public async Task<Project?> GetProjectDetailAsync(int id)
        {
            return await _db.Projects
                .Include(p => p.Vessel)
                .Include(p => p.Impeller)
                .Include(p => p.FluidProperties)
                .Include(p => p.CalculationResult)
                .FirstOrDefaultAsync(p => p.Id == id);
        }

        public async Task<(bool success, string? error, int projectId)> CreateProjectAsync(
            ProjectFormViewModel vm, string tenantId, string? userId)
        {
            var project = new Project
            {
                Name = vm.Name,
                Description = vm.Description,
                TenantId = tenantId,
                CreatedByUserId = userId,
                CreatedAt = DateTime.UtcNow,
                Vessel = new Vessel
                {
                    DiameterM = vm.VesselDiameterM,
                    LiquidHeightM = vm.LiquidHeightM,
                    HasBaffles = vm.HasBaffles,
                    NumberOfBaffles = vm.NumberOfBaffles
                },
                Impeller = new Impeller
                {
                    Type = vm.ImpellerType,
                    DiameterM = vm.ImpellerDiameterM,
                    RotationalSpeedRpm = vm.RotationalSpeedRpm,
                    NumberOfImpellers = vm.NumberOfImpellers
                },
                FluidProperties = new FluidProperties
                {
                    DensityKgM3 = vm.DensityKgM3,
                    ViscosityPaS = vm.ViscosityPaS
                }
            };

            _db.Projects.Add(project);
            await _db.SaveChangesAsync();

            return (true, null, project.Id);
        }

        public async Task<(bool success, string? error)> UpdateProjectAsync(int id, ProjectFormViewModel vm, string? userId)
        {
            var project = await _db.Projects
                .Include(p => p.Vessel)
                .Include(p => p.Impeller)
                .Include(p => p.FluidProperties)
                .FirstOrDefaultAsync(p => p.Id == id);

            if (project is null)
                return (false, $"Project {id} not found.");

            project.Name = vm.Name;
            project.Description = vm.Description;
            project.UpdatedAt = DateTime.UtcNow;

            if (project.Vessel is not null)
            {
                project.Vessel.DiameterM = vm.VesselDiameterM;
                project.Vessel.LiquidHeightM = vm.LiquidHeightM;
                project.Vessel.HasBaffles = vm.HasBaffles;
                project.Vessel.NumberOfBaffles = vm.NumberOfBaffles;
            }

            if (project.Impeller is not null)
            {
                project.Impeller.Type = vm.ImpellerType;
                project.Impeller.DiameterM = vm.ImpellerDiameterM;
                project.Impeller.RotationalSpeedRpm = vm.RotationalSpeedRpm;
                project.Impeller.NumberOfImpellers = vm.NumberOfImpellers;
            }

            if (project.FluidProperties is not null)
            {
                project.FluidProperties.DensityKgM3 = vm.DensityKgM3;
                project.FluidProperties.ViscosityPaS = vm.ViscosityPaS;
            }

            await _db.SaveChangesAsync();
            return (true, null);
        }

        public async Task<(bool success, string? error)> DeleteProjectAsync(int id)
        {
            var project = await _db.Projects.FirstOrDefaultAsync(p => p.Id == id);
            if (project is null)
                return (false, $"Project {id} not found.");

            _db.Projects.Remove(project);
            await _db.SaveChangesAsync();
            return (true, null);
        }

        public async Task<bool> IsProjectExistsAsync(int id)
        {
            return await _db.Projects.AnyAsync(p => p.Id == id);
        }

        public async Task<bool> CanEditProjectAsync(int id, string tenantId)
        {
            return await _db.Projects.AnyAsync(p => p.Id == id && p.TenantId == tenantId);
        }
    }
}