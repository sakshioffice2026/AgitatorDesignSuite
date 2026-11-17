using Agitator.Core.Entities;

namespace Agitator.Repositories.Contracts
{
    public interface IShaftMaterialRepository
    {
        /// <summary>All active shaft materials, ordered by grade then heat treatment (for dropdowns).</summary>
        Task<List<ShaftMaterial>> GetAllActiveAsync();

        /// <summary>One shaft material with its temperature derating points loaded, ordered by temperature.</summary>
        Task<ShaftMaterial?> GetWithDeratingAsync(int id);
    }
}
