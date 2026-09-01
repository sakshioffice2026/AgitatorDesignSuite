using Agitator.Core;
using Agitator.Core.Entities;
using Agitator.Repositories.Contracts;

namespace Agitator.Repositories.Repositories
{
    public class ExceptionHandlerRepository : IExceptionHandlerRepository
    {
        private readonly ApplicationDbContext _db;

        public ExceptionHandlerRepository(ApplicationDbContext db)
        {
            _db = db;
        }

        public async Task SaveException(string className, string methodName, Exception error)
        {
            var entry = new ExceptionHandler
            {
                ClassName = className,
                MethodName = methodName,
                ErrorMessage = error.Message,
                StackTrace = error.StackTrace,
                CreatedAt = DateTime.UtcNow
            };

            _db.ExceptionHandlers.Add(entry);
            await _db.SaveChangesAsync();
        }
    }
}