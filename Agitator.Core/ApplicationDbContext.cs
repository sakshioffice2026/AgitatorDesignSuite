

using Agitator.Core.Entities;
using Microsoft.EntityFrameworkCore;

namespace Agitator.Core
{
    public class ApplicationDbContext : DbContext
    {
        public ApplicationDbContext(DbContextOptions<ApplicationDbContext> options)
            : base(options)
        {
        }

        public DbSet<Project> Projects => Set<Project>();
        public DbSet<Vessel> Vessels => Set<Vessel>();
        public DbSet<Impeller> Impellers => Set<Impeller>();
        public DbSet<FluidProperties> FluidProperties => Set<FluidProperties>();
        public DbSet<CalculationResult> CalculationResults => Set<CalculationResult>();
        public DbSet<ExceptionHandler> ExceptionHandlers => Set<ExceptionHandler>();

        protected override void OnModelCreating(ModelBuilder modelBuilder)
        {
            base.OnModelCreating(modelBuilder);

            modelBuilder.Entity<Project>(entity =>
            {
                entity.HasIndex(p => p.TenantId);

                entity.HasOne(p => p.Vessel)
                      .WithOne(v => v.Project!)
                      .HasForeignKey<Vessel>(v => v.ProjectId)
                      .OnDelete(DeleteBehavior.Cascade);

                entity.HasOne(p => p.Impeller)
                      .WithOne(i => i.Project!)
                      .HasForeignKey<Impeller>(i => i.ProjectId)
                      .OnDelete(DeleteBehavior.Cascade);

                entity.HasOne(p => p.FluidProperties)
                      .WithOne(f => f.Project!)
                      .HasForeignKey<FluidProperties>(f => f.ProjectId)
                      .OnDelete(DeleteBehavior.Cascade);

                entity.HasOne(p => p.CalculationResult)
                      .WithOne(r => r.Project!)
                      .HasForeignKey<CalculationResult>(r => r.ProjectId)
                      .OnDelete(DeleteBehavior.Cascade);
            });

            // Store enums as readable strings in MySQL instead of raw ints —
            // much easier to debug/query directly in the database.
            modelBuilder.Entity<Vessel>()
                .Property(v => v.HeadType)
                .HasConversion<string>()
                .HasMaxLength(30);

            modelBuilder.Entity<Impeller>()
                .Property(i => i.Type)
                .HasConversion<string>()
                .HasMaxLength(30);

            modelBuilder.Entity<CalculationResult>()
                .Property(r => r.Regime)
                .HasConversion<string>()
                .HasMaxLength(20);

            modelBuilder.Entity<CalculationResult>()
                .Property(r => r.CadStatus)
                .HasConversion<string>()
                .HasMaxLength(20);

            modelBuilder.Entity<ExceptionHandler>(entity =>
            {
                entity.ToTable("exceptionhandler");
            });
        }
    }
}