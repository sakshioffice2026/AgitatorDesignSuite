namespace Agitator.Repositories.Contracts
{
    public interface IUnitOfWork
    {
        IProjectRepository projectRepository { get; }
        ICalculationResultRepository calculationResultRepository { get; }
        IExceptionHandlerRepository exceptionHandlerRepository { get; }

        Task<int> Commit();
    }
}