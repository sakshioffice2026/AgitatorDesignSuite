using Agitator.Core.Entities;
using Agitator.Models.DTOs;
namespace Agitator.Business.Contracts
{
    public interface IAgitatorCalculationService
    {
        /// <summary>
        /// Runs the full process + mechanical calculation set for a project
        /// and returns a populated (but not yet persisted) CalculationResult.
        /// </summary>
        CalculationResult Calculate(Vessel vessel, Impeller impeller, FluidProperties fluid);


        /// <summary>
        /// Builds the three-way comparison (manual formula, FEA, PINN) and the design checks
        /// (strength, deflection, critical speed, fatigue) for the Results page.
        /// FEA values are read from <c>calculation.FeaCheckJson</c> when present.
        /// <paramref name="prediction"/> may be null when the PINN service was not called.
        /// </summary>
        ShaftComparisonResult BuildShaftComparison(
            Vessel vessel,
            Impeller impeller,
            FluidProperties fluid,
            CalculationResult calculation,
            ShaftPredictionResult? prediction);
    }
}