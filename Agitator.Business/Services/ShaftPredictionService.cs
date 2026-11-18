using System.Net.Http.Json;
using System.Text.Json;
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
        private readonly ShaftComparisonOptions _comparison;
        private readonly ILogger<ShaftPredictionService> _logger;

        public ShaftPredictionService(
            HttpClient httpClient,
            IOptions<ShaftPredictionOptions> options,
            IOptions<ShaftComparisonOptions> comparison,
            ILogger<ShaftPredictionService> logger)
        {
            _httpClient = httpClient;
            _options = options.Value;
            _comparison = comparison.Value;
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

            // Formula result from the existing .NET engine (includes Km = 1.5).
            double dotNetFormulaMm = calculation.RecommendedShaftDiameterMm;
            var physics = response.Physics;
            var fea = ReadFea(calculation.FeaCheckJson);

            double gapPercent = dotNetFormulaMm > 0
                ? (response.RawPredictedDiameterMm - dotNetFormulaMm) / dotNetFormulaMm * 100.0
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

            if (fea is not null)
            {
                // Trust is judged against FEA first.
                if (physics is null)
                {
                    isTrusted = false;
                    notes.Add("Service returned no physics block; PINN cannot be compared with FEA.");
                }
                else
                {
                    isTrusted &= WithinFea("Tip deflection", physics.TipDeflectionMm, fea.TipDeflectionMm,
                        _comparison.DeflectionTolerancePercent, notes);
                    isTrusted &= WithinFea("Angle of twist", physics.TwistDeg, fea.TwistDeg,
                        _comparison.TwistTolerancePercent, notes);
                    isTrusted &= WithinFea("Von Mises stress", physics.VonMisesStressMpa, fea.VonMisesMpa,
                        _comparison.DiameterTolerancePercent, notes);
                    isTrusted &= WithinFea("Max shear stress", physics.MaxShearStressMpa, fea.MaxShearMpa,
                        _comparison.DiameterTolerancePercent, notes);
                }
            }
            else
            {
                // No FEA: fall back to the formula.
                notes.Add("FEA result not available; PINN judged against the formula only.");

                if (Math.Abs(gapPercent) > _options.TolerancePercent)
                {
                    isTrusted = false;
                    notes.Add($"Gap to formula {gapPercent:0.##} % exceeds tolerance {_options.TolerancePercent:0.##} %.");
                }
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
                RawFormulaDiameterMm = dotNetFormulaMm,
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
                Physics = response.Physics
            };
        }

        private static bool WithinFea(
            string name,
            double? pinn,
            double? fea,
            double tolerancePercent,
            List<string> notes)
        {
            double? gap = ShaftComparisonMath.PercentDifference(pinn, fea);

            if (!gap.HasValue || Math.Abs(gap.Value) <= tolerancePercent)
            {
                return true;
            }

            notes.Add($"{name}: PINN differs from FEA by {gap.Value:0.##} % (limit {tolerancePercent:0.##} %).");
            return false;
        }

        private ShaftFeaResult? ReadFea(string? json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return null;
            }

            try
            {
                var fea = JsonSerializer.Deserialize<ShaftFeaResult>(json);
                return fea is not null && fea.IsOk ? fea : null;
            }
            catch (JsonException ex)
            {
                _logger.LogWarning(ex, "FEA result JSON could not be read.");
                return null;
            }
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