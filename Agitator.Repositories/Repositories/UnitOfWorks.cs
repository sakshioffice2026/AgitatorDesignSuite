using Agitator.Core;
using Agitator.Repositories.Contracts;
using Agitator.Repositories.Repositories;

namespace Agitator.Repositories
{
    public class UnitOfWorks : IUnitOfWork
    {
        private readonly ApplicationDbContext _db;

        public IProjectRepository projectRepository { get; }
        public ICalculationResultRepository calculationResultRepository { get; }
        public IExceptionHandlerRepository exceptionHandlerRepository { get; }

        public UnitOfWorks(ApplicationDbContext db)
        {
            _db = db;
            projectRepository = new ProjectRepository(_db);
            calculationResultRepository = new CalculationResultRepository(_db);
            exceptionHandlerRepository = new ExceptionHandlerRepository(_db);
        }

        public async Task<int> Commit()
        {
            return await _db.SaveChangesAsync();
        }
    }
}