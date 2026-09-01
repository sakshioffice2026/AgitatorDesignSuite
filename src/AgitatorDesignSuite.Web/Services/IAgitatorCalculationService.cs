using Agitator.Core.Entities;


namespace AgitatorDesignSuite.Web.Services
{
    public interface IAgitatorCalculationService
    {
        /// <summary>
        /// Runs the full process + mechanical calculation set for a project
        /// and returns a populated (but not yet persisted) CalculationResult.
        /// </summary>
        CalculationResult Calculate(Vessel vessel, Impeller impeller, FluidProperties fluid);
    }
}
