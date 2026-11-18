using Agitator.Core.Entities;
using Agitator.Models.DTOs;

namespace Agitator.Business.Contracts
{
    public interface IShaftPredictionService
    {
        /// <summary>
        /// Calls the Python PINN service for the shaft diameter and physics block, runs the FEA check,
        /// and compares PINN, formula (Km = 1.5) and FEA results against the tolerances in
        /// <c>ShaftComparisonOptions</c>.
        /// Trust is judged against FEA first, then against the formula.
        /// The formula result is never replaced.
        /// Throws <see cref="InvalidOperationException"/> if the service is unreachable or returns an invalid response.
        /// </summary>
        Task<ShaftPredictionResult> PredictAsync(
    Vessel vessel,
    Impeller impeller,
    FluidProperties fluid,
    CalculationResult calculation,
    CancellationToken cancellationToken = default);
    }
}