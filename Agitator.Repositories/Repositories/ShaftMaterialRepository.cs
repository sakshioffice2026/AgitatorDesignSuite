using Agitator.Core;
using Agitator.Core.Entities;
using Agitator.Repositories.Contracts;
using Microsoft.EntityFrameworkCore;

namespace Agitator.Repositories.Repositories
{
    public class ShaftMaterialRepository : IShaftMaterialRepository
    {
        private readonly ApplicationDbContext _db;

        public ShaftMaterialRepository(ApplicationDbContext db)
        {
            _db = db;
        }

        public async Task<List<ShaftMaterial>> GetAllActiveAsync()
        {
            return await _db.ShaftMaterials
                .AsNoTracking()
                .Where(m => m.IsActive)
                .OrderBy(m => m.Grade)
                .ThenBy(m => m.HeatTreatment)
                .ToListAsync();
        }

        public async Task<ShaftMaterial?> GetWithDeratingAsync(int id)
        {
            var material = await _db.ShaftMaterials
                .AsNoTracking()
                .Include(m => m.TempDeratingPoints)
                .FirstOrDefaultAsync(m => m.Id == id);

            if (material is not null)
            {
                material.TempDeratingPoints = material.TempDeratingPoints
                    .OrderBy(d => d.TemperatureC)
                    .ToList();
            }

            return material;
        }
    }
}
