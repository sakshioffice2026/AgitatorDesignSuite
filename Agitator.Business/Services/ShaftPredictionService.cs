using System.Net.Http.Json;
using Agitator.Business.Contracts;
using Agitator.Core.Entities;
using Agitator.Models.DTOs;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Agitator.Business.Services
{
    /// <summary>
    /// Calls the Python PINN service (POST /predict), cross-checks the answer against the
    /// trusted formula result and sets the trusted flag. Never replaces the formula result.
    /// Registered as a typed HttpClient.
    /// </summary>
    public class ShaftPredictionService : IShaftPredictionService
    {
        private readonly HttpClient _httpClient;
        private readonly ShaftPredictionOptions _options;
        private readonly ILogger<ShaftPredictionService> _logger;

        public ShaftPredictionService(
            HttpClient httpClient,
            IOptions<ShaftPredictionOptions> options,
            ILogger<ShaftPredictionService> logger)
        {
            _httpClient = httpClient;
            _options = options.Value;
            _logger = logger;
        }

        public async Task<ShaftPredictionResult> PredictAsync(
    Vessel vessel,
    Impeller impeller,
    FluidProperties fluid,
    CalculationResult calculation,
    CancellationToken cancellationToken = default)
        {
            if (impeller is null) throw new ArgumentNullException(nameof(impeller));
            if (fluid is null) throw new ArgumentNullException(nameof(fluid));
            if (calculation is null) throw new ArgumentNullException(nameof(calculation));

            double allowableShearPa = AgitatorCalculationService.ComputeAllowableShearStressPa(
                impeller.ShaftMaterial, fluid.OperatingTemperatureC);

            var request = new ShaftPredictionRequest
            {
                RatedTorqueNm = calculation.TorqueNm,
                ImpellerDiameterM = impeller.DiameterM,
                LiquidHeightM = vessel.LiquidHeightM,
                AllowableShearStressMpa = allowableShearPa / 1_000_000.0,
                OperatingTemperatureC = fluid.OperatingTemperatureC,
                ShaftMaterialName = impeller.ShaftMaterial?.DisplayName ?? string.Empty
            };

            var response = await CallServiceAsync(request, cancellationToken);

            // Formula result from the existing .NET engine (the trusted source).
            double dotNetFormulaMm = calculation.RecommendedShaftDiameterMm;

            var notes = new List<string>();
            bool isTrusted = true;

            if (!response.WithinTrustedRange)
            {
                isTrusted = false;
            }

            if (Math.Abs(response.GapPercent) > _options.TolerancePercent)
            {
                isTrusted = false;
                notes.Add($"Gap to formula {response.GapPercent:0.##} % exceeds tolerance {_options.TolerancePercent:0.##} %.");
            }

            if (response.ShearStressMpa > response.AllowableShearStressMpa)
            {
                isTrusted = false;
            }

            // The Python formula must agree with the .NET formula. If not, units or constants differ.
            if (Math.Abs(response.FormulaShaftDiameterMm - dotNetFormulaMm) > 0.5)
            {
                isTrusted = false;
                notes.Add($"Python formula ({response.FormulaShaftDiameterMm:0.#} mm) differs from .NET formula ({dotNetFormulaMm:0.#} mm). Check units and constants.");
            }

            if (!string.IsNullOrWhiteSpace(response.Notes))
            {
                notes.Add(response.Notes);
            }

            return new ShaftPredictionResult
            {
                RawFormulaDiameterMm = response.RawFormulaDiameterMm,
                FormulaShaftDiameterMm = dotNetFormulaMm,
                PredictedShaftDiameterMm = response.PredictedShaftDiameterMm,
                RawPredictedDiameterMm = response.RawPredictedDiameterMm,
                GapPercent = response.GapPercent,
                ShearStressMpa = response.ShearStressMpa,
                AllowableShearStressMpa = response.AllowableShearStressMpa,
                IsTrusted = isTrusted,
                Notes = string.Join(" ", notes),
                ModelVersion = response.ModelVersion,
                GeneratedAtUtc = DateTime.UtcNow,
                ShaftMaterialName = impeller.ShaftMaterial?.Grade ?? string.Empty,
                HeatTreatment = impeller.ShaftMaterial?.HeatTreatment ?? string.Empty,
                OperatingTemperatureC = fluid.OperatingTemperatureC,
                YieldStrengthAtTempMpa = impeller.ShaftMaterial is null
                    ? 0
                    : Math.Round(AgitatorCalculationService.ComputeYieldAtTemperatureMpa(
                        impeller.ShaftMaterial, fluid.OperatingTemperatureC), 1)
            };
        }

        private async Task<ShaftPredictionServiceResponse> CallServiceAsync(
            ShaftPredictionRequest request,
            CancellationToken cancellationToken)
        {
            string url = $"{_options.BaseUrl.TrimEnd('/')}/predict";
            int attempts = Math.Max(1, _options.RetryCount + 1);
            Exception? lastError = null;

            for (int attempt = 1; attempt <= attempts; attempt++)
            {
                using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                timeoutCts.CancelAfter(TimeSpan.FromSeconds(_options.TimeoutSeconds));

                try
                {
                    using var httpResponse = await _httpClient.PostAsJsonAsync(url, request, timeoutCts.Token);

                    if (!httpResponse.IsSuccessStatusCode)
                    {
                        string body = await httpResponse.Content.ReadAsStringAsync(cancellationToken);
                        throw new HttpRequestException(
                            $"Prediction service returned {(int)httpResponse.StatusCode}: {body}");
                    }

                    var result = await httpResponse.Content
                        .ReadFromJsonAsync<ShaftPredictionServiceResponse>(cancellationToken: timeoutCts.Token);

                    if (result is null || result.PredictedShaftDiameterMm <= 0)
                    {
                        throw new InvalidOperationException("Prediction service returned an empty or invalid response.");
                    }

                    return result;
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    lastError = ex;
                    _logger.LogWarning(ex, "Shaft prediction attempt {Attempt}/{Attempts} failed.", attempt, attempts);
                }
            }

            throw new InvalidOperationException(
                "The shaft prediction service is not available right now. Please try again later.",
                lastError);
        }
    }
}