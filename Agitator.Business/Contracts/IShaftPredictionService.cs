using Agitator.Core.Entities;
using Agitator.Models.DTOs;

namespace Agitator.Business.Contracts
{
    public interface IShaftPredictionService
    {
        /// <summary>
        /// Calls the Python PINN service for the shaft diameter, cross-checks it against
        /// the formula result already stored in <paramref name="calculation"/>, and returns
        /// the comparison with a trusted / not trusted flag.
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