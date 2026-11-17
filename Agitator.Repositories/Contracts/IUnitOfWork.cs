namespace Agitator.Repositories.Contracts
{
    public interface IUnitOfWork
    {
        IProjectRepository projectRepository { get; }
        ICalculationResultRepository calculationResultRepository { get; }
        IExceptionHandlerRepository exceptionHandlerRepository { get; }

        IShaftMaterialRepository shaftMaterialRepository { get; }

        Task<int> Commit();
    }
}