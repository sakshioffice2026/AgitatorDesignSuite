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
    /// FEA and tolerance checks are judged later, when the comparison is built.
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

            var material = impeller.ShaftMaterial;

            double allowableShearPa = AgitatorCalculationService.ComputeAllowableShearStressPa(
                material, fluid.OperatingTemperatureC);

            double yieldAtTempMpa = material is null
                ? 0.0
                : AgitatorCalculationService.ComputeYieldAtTemperatureMpa(material, fluid.OperatingTemperatureC);
            double ultimateMpa = (double?)material?.UltimateStrengthMpa ?? 0.0;

            var request = new ShaftPredictionRequest
            {
                RatedTorqueNm = calculation.TorqueNm,
                ImpellerDiameterM = impeller.DiameterM,
                LiquidHeightM = vessel.LiquidHeightM,
                AllowableShearStressMpa = allowableShearPa / 1_000_000.0,
                OperatingTemperatureC = fluid.OperatingTemperatureC,
                ShaftMaterialName = impeller.ShaftMaterial?.DisplayName ?? string.Empty,
                OperatingSpeedRpm = impeller.RotationalSpeedRpm,
                ImpellerMassKg = (double?)impeller.ImpellerMassKg ?? 0.0,
                ElasticModulusGpa = (double?)material?.ElasticModulusGpa ?? 200.0,
                PoissonRatio = (double?)material?.PoissonRatio ?? 0.30,
                DensityKgM3 = (double?)material?.DensityKgM3 ?? 7850.0,
                YieldStrengthMpa = yieldAtTempMpa,
                UltimateStrengthMpa = ultimateMpa,
                EnduranceLimitMpa = (double?)material?.EnduranceLimitMpa ?? 0.5 * ultimateMpa,
                SurfaceFinish = material?.SurfaceFinish ?? "machined",
                ShaftDiameterMm = calculation.RecommendedShaftDiameterMm
            };

            var response = await CallServiceAsync(request, cancellationToken);

            // Standard size recommended by the .NET engine (stress, deflection, critical speed, fatigue).
            double dotNetFormulaMm = calculation.RecommendedShaftDiameterMm;

            // Raw (unrounded) stress-only formula diameter from the service, the same basis as its PINN fallback.
            double rawFormulaMm = response.RawFormulaDiameterMm > 0
                ? response.RawFormulaDiameterMm
                : dotNetFormulaMm;

            double gapPercent = rawFormulaMm > 0
                ? (response.RawPredictedDiameterMm - rawFormulaMm) / rawFormulaMm * 100.0
                : response.GapPercent;

            var notes = new List<string>();
            bool isTrusted = true;

            if (!response.WithinTrustedRange)
            {
                isTrusted = false;
            }

            if (response.ShearStressMpa > response.AllowableShearStressMpa)
            {
                isTrusted = false;
            }

            // The Python formula must agree with the .NET formula. If not, units or constants differ.
            if (response.FormulaShaftDiameterMm - dotNetFormulaMm > 0.5)
            {
                isTrusted = false;
                notes.Add($"Python formula ({response.FormulaShaftDiameterMm:0.#} mm) is larger than the recommended size ({dotNetFormulaMm:0.#} mm). Check units and constants.");
            }

            if (!string.IsNullOrWhiteSpace(response.Notes))
            {
                notes.Add(response.Notes);
            }

            return new ShaftPredictionResult
            {
                RawFormulaDiameterMm = rawFormulaMm,
                FormulaShaftDiameterMm = dotNetFormulaMm,
                PredictedShaftDiameterMm = response.PredictedShaftDiameterMm,
                RawPredictedDiameterMm = response.RawPredictedDiameterMm,
                GapPercent = gapPercent,
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
                        impeller.ShaftMaterial, fluid.OperatingTemperatureC), 1),
                ShaftLengthM = response.ShaftLengthM > 0
                    ? response.ShaftLengthM
                    : AgitatorCalculationService.ComputeShaftLengthM(vessel.LiquidHeightM),
                Physics = response.Physics,
                WithinTrustedRange = response.WithinTrustedRange
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