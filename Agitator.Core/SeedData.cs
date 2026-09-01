using Microsoft.EntityFrameworkCore;

namespace Agitator.Core
{
    /// <summary>Idempotent startup seed hook. No seed rows required yet for this domain — kept as a no-op entry point per pattern.</summary>
    public static class SeedData
    {
        public static async Task InitializeAsync(ApplicationDbContext db)
        {
            await db.Database.EnsureCreatedAsync();
            // No reference/lookup data to seed yet.
        }
    }
}