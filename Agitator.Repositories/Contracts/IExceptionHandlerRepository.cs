namespace Agitator.Repositories.Contracts
{
    public interface IExceptionHandlerRepository
    {
        Task SaveException(string className, string methodName, Exception error);
    }
}