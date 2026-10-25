
from pathlib import Path
import json
import re
import sys

import ollama
from flask import Flask, jsonify, render_template, request


# --------------------------------------------------
# Paths and imports
# --------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data" / "data1output"

# Allow imports when running app.py directly from the project root.
sys.path.insert(0, str(DATA_DIR))

try:
    from data.data1output.risk_calc import calculate_risk
    CALCULATOR_READY = True
except ImportError:
    try:
        from risk_calc import calculate_risk
        CALCULATOR_READY = True
    except ImportError as error:
        calculate_risk = None
        CALCULATOR_READY = False
        print(f"Risk calculator import failed: {error}")


try:
    from data.data1output.outage_analysis import analyze_outage
    ANALYSIS_READY = True
except ImportError:
    try:
        from outage_analysis import analyze_outage
        ANALYSIS_READY = True
    except ImportError as error:
        analyze_outage = None
        ANALYSIS_READY = False
        print(f"Outage analysis import failed: {error}")


# --------------------------------------------------
# Flask configuration
# --------------------------------------------------

app = Flask(__name__)

OLLAMA_MODEL = "qwen3:4b"
MAX_MESSAGE_LENGTH = 5000


# --------------------------------------------------
# Equipment / circuit ID extraction
# --------------------------------------------------

def get_equipment_id(message):
    """
    Extract a likely equipment or circuit ID from a message.

    Supports CIR-prefixed circuit IDs and numeric IDs with at
    least 10 digits. Identifiers are kept as strings.
    """
    if not isinstance(message, str):
        return None

    circuit_match = re.search(
        r"\bCIR[A-Z0-9_-]*\b",
        message,
        re.IGNORECASE,
    )

    if circuit_match:
        return circuit_match.group(0)

    numeric_match = re.search(r"\b\d{10,}\b", message)

    if numeric_match:
        return numeric_match.group(0)

    return None


# --------------------------------------------------
# Human-readable summaries
# --------------------------------------------------

def summarize_analysis(analysis):
    """Create a deterministic summary from the analysis results."""

    risk = analysis.get("risk", {})
    asset = analysis.get("asset")
    labor_result = analysis.get("labor_estimate")

    if risk.get("error"):
        return (
            "I couldn't calculate a reliable outage-impact estimate for "
            f"this identifier: {risk['error']}"
        )

    affected = risk.get("estimated_affected_service_locations")
    rating = risk.get("rating", "Unknown")
    confidence = risk.get("data_confidence", risk.get("confidence", "Unknown"))

    parts = []

    if asset:
        category = asset.get("category", "unknown equipment")
        parts.append(
            f"The identified equipment category is {category}."
        )
    else:
        parts.append(
            "The network data was checked, but a supported equipment "
            "record was not identified for a repair estimate."
        )

    if affected is not None:
        parts.append(
            f"The estimated outage impact is {rating.lower()}, "
            f"affecting approximately {affected:,} distinct service "
            f"locations in the dataset."
        )

    if "percent_of_dataset_affected" in risk:
        parts.append(
            f"That represents {risk['percent_of_dataset_affected']:.2f}% "
            "of the service locations in the dataset."
        )

    if confidence != "Unknown":
        parts.append(f"Data confidence: {confidence}.")

    if risk.get("storm_scenario"):
        parts.append(
            f"The risk calculation uses the assumed "
            f"'{risk['storm_scenario']}' storm scenario. "
            "The storm-adjusted score is not a probability of failure."
        )

    if isinstance(labor_result, dict):
        if labor_result.get("error"):
            parts.append(
                f"Repair labor could not be estimated: "
                f"{labor_result['error']}"
            )
        elif labor_result.get("total_labor_hours") is not None:
            hours = labor_result["total_labor_hours"]
            parts.append(
                f"The assumed replacement tasks total {hours:.2f} "
                "labor-hours. This is not an estimate of elapsed "
                "restoration time."
            )
    elif not asset:
        parts.append(
            "No repair labor estimate is available because the required "
            "repair tasks could not be identified."
        )

    parts.append(
        "This analysis estimates potential impact from the network data. "
        "It does not confirm physical damage or prove that replacement "
        "is necessary."
    )

    return " ".join(parts)


# --------------------------------------------------
# Rule-based analysis
# --------------------------------------------------

def get_rule_based_response(message):
    """
    Return a deterministic reply and optional structured analysis.
    The structured details are sent separately to the frontend.
    """
    identifier = get_equipment_id(message)

    if identifier:
        if ANALYSIS_READY:
            analysis = analyze_outage(identifier)
            return {
                "reply": summarize_analysis(analysis),
                "details": analysis,
            }

        if CALCULATOR_READY:
            risk = calculate_risk(identifier)
            return {
                "reply": (
                    "I checked the network risk calculator, but the "
                    "repair-labor integration is unavailable."
                ),
                "details": {
                    "equipment_id": identifier,
                    "risk": risk,
                    "asset": None,
                    "repair_tasks": [],
                    "labor_estimate": None,
                },
            }

        return {
            "reply": (
                "The outage analysis tools are not available. Check the "
                "imports and paths for risk_calc.py and outage_analysis.py."
            ),
            "details": None,
        }

    message_lower = message.lower()

    if any(
        phrase in message_lower
        for phrase in (
            "how long until power",
            "restoration time",
            "when will power",
            "when will service",
        )
    ):
        return {
            "reply": (
                "This prototype estimates network impact and labor-hours "
                "for assumed repair tasks. It does not currently predict "
                "elapsed restoration time, crew travel, dispatch, material "
                "availability, or task dependencies. Provide an equipment "
                "or circuit ID to analyze potential impact."
            ),
            "details": None,
        }

    if any(
        phrase in message_lower
        for phrase in (
            "storm preparation",
            "prepare for a storm",
            "storm preparedness",
        )
    ):
        return {
            "reply": (
                "For storm preparation, review severe-weather forecasts, "
                "confirm emergency procedures, check backup-power plans, "
                "and follow official utility updates. This prototype's "
                "storm factor is an illustrative assumption, not a "
                "forecast of equipment failure."
            ),
            "details": None,
        }

    return {
        "reply": (
            "I can estimate potential outage impact and, when the asset "
            "can be identified, labor-hours for an assumed replacement. "
            "Please include an equipment ID, network location ID, or "
            "circuit ID in your question."
        ),
        "details": None,
    }


# --------------------------------------------------
# Ollama response generation
# --------------------------------------------------

def get_response(message):
    """
    Generate a conversational response using Ollama while keeping
    network and labor calculations grounded in Python results.
    """
    base_result = get_rule_based_response(message)

    reply = base_result["reply"]
    details = base_result.get("details")

    context = {
        "calculated_summary": reply,
        "structured_analysis": details,
    }

    system_prompt = """
You are GridAssist, an AI Outage Concierge prototype.

Rules:
- Use the supplied Python analysis as the source of truth for calculated
  outage impact, equipment identification, and labor estimates.
- Do not invent affected-customer counts, equipment damage, repair tasks,
  labor-hours, or restoration times.
- A high impact rating does not mean the equipment is confirmed damaged.
- A replacement task is an assumption unless the data explicitly confirms
  the repair required.
- Labor-hours are not elapsed restoration time.
- The storm-adjusted score is not a probability of equipment failure.
- If a field is missing or an analysis failed, say so plainly.
- Give concise, helpful answers.
- If the user has not provided an ID, ask them for one when needed.
"""

    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        f"User question:\n{message}\n\n"
                        "Verified Python analysis context:\n"
                        f"{json.dumps(context, indent=2, default=str)}"
                    ),
                },
            ],
        )

        model_reply = response.get("message", {}).get("content", "").strip()

        if model_reply:
            reply = model_reply

    except Exception as error:
        # Keep the application usable even if Ollama is unavailable.
        print(f"Ollama request failed: {error}")

    return {
        "reply": reply,
        "details": details,
    }


# --------------------------------------------------
# Routes
# --------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")

    if not isinstance(message, str):
        return jsonify({
            "error": "Message must be a string."
        }), 400

    message = message.strip()

    if not message:
        return jsonify({
            "error": "Please enter a message."
        }), 400

    if len(message) > MAX_MESSAGE_LENGTH:
        return jsonify({
            "error": (
                f"Message must be no longer than "
                f"{MAX_MESSAGE_LENGTH} characters."
            )
        }), 400

    try:
        result = get_response(message)
        return jsonify(result)

    except Exception as error:
        app.logger.exception("Unexpected error while processing chat")

        return jsonify({
            "reply": (
                "I encountered an unexpected error while processing "
                "that request. Please try again."
            ),
            "details": None,
            "error": str(error),
        }), 500


@app.route("/health", methods=["GET"])
def health():
    """Simple endpoint to check whether Flask and imports are available."""
    return jsonify({
        "status": "ok",
        "risk_calculator_ready": CALCULATOR_READY,
        "outage_analysis_ready": ANALYSIS_READY,
        "ollama_model": OLLAMA_MODEL,
    })


if __name__ == "__main__":
    app.run(debug=True)
