
document.addEventListener("DOMContentLoaded", () => {
    // --------------------------------------------------
    // Find the chat elements in index.html
    // --------------------------------------------------

    const chatForm = document.querySelector(
        "#chat-form, #chatForm, [data-chat-form]"
    );

    const messageInput = document.querySelector(
        "#message-input, #messageInput, #user-input, #userInput, " +
        'textarea[name="message"], input[name="message"]'
    );

    const chatMessages = document.querySelector(
        "#chat-messages, #chatMessages, #messages, " +
        ".chat-messages, [data-chat-messages]"
    );

    const sendButton = document.querySelector(
        "#send-button, #sendButton, #send-btn, #sendBtn, " +
        'button[type="submit"]'
    );

    if (!chatForm || !messageInput || !chatMessages) {
        console.error(
            "GridAssist chat elements were not found. Check the IDs in index.html.",
            {
                chatForm,
                messageInput,
                chatMessages,
            }
        );
        return;
    }

    // --------------------------------------------------
    // General helpers
    // --------------------------------------------------

    function createElement(tag, className, text = "") {
        const element = document.createElement(tag);

        if (className) {
            element.className = className;
        }

        if (text !== undefined && text !== null) {
            element.textContent = String(text);
        }

        return element;
    }

    function appendMessage(role, text) {
        const message = createElement(
            "div",
            `message ${role}-message`
        );

        const content = createElement("div", "message-content", text);

        message.appendChild(content);
        chatMessages.appendChild(message);

        scrollToLatest();

        return message;
    }

    function scrollToLatest() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function formatNumber(value, decimals = 2) {
        const number = Number(value);

        if (!Number.isFinite(number)) {
            return "Unavailable";
        }

        return number.toLocaleString(undefined, {
            maximumFractionDigits: decimals,
        });
    }

    function formatLabel(value) {
        if (!value) {
            return "Unknown";
        }

        return String(value)
            .replaceAll("_", " ")
            .replace(/\b\w/g, character => character.toUpperCase());
    }

    function addField(container, label, value) {
        const row = createElement("div", "analysis-field");
        const labelElement = createElement("span", "analysis-label", label);
        const valueElement = createElement(
            "span",
            "analysis-value",
            value ?? "Unavailable"
        );

        row.append(labelElement, valueElement);
        container.appendChild(row);

        return row;
    }

    function addSection(container, title) {
        const section = createElement("section", "analysis-section");
        const heading = createElement("h4", "analysis-heading", title);

        section.appendChild(heading);
        container.appendChild(section);

        return section;
    }

    // --------------------------------------------------
    // Risk / labor analysis display
    // --------------------------------------------------

    function renderAnalysis(details) {
        if (!details || typeof details !== "object") {
            return null;
        }

        const panel = createElement("div", "analysis-panel");
        const title = createElement(
            "h3",
            "analysis-title",
            "Outage Analysis"
        );

        panel.appendChild(title);

        if (details.equipment_id) {
            addField(panel, "Equipment / Circuit ID", details.equipment_id);
        }

        const asset = details.asset;
        const risk = details.risk;
        const labor = details.labor_estimate;

        // Equipment identification
        if (asset) {
            const assetSection = addSection(panel, "Equipment");

            addField(
                assetSection,
                "Category",
                formatLabel(asset.category)
            );

            if (asset.location) {
                addField(assetSection, "Network Location", asset.location);
            }

            if (asset.size !== null && asset.size !== undefined) {
                addField(assetSection, "Size", `${asset.size} kVA`);
            }
        } else if (details.equipment_id) {
            const assetSection = addSection(panel, "Equipment");

            addField(
                assetSection,
                "Identification",
                "No supported asset record found"
            );
        }

        // Network impact
        if (risk && typeof risk === "object") {
            const riskSection = addSection(panel, "Estimated Network Impact");

            if (risk.error) {
                addField(riskSection, "Status", risk.error);
            } else {
                addField(
                    riskSection,
                    "Impact Rating",
                    risk.rating || "Unavailable"
                );

                if (
                    risk.estimated_affected_service_locations !== undefined
                ) {
                    addField(
                        riskSection,
                        "Affected Service Locations",
                        formatNumber(
                            risk.estimated_affected_service_locations,
                            0
                        )
                    );
                }

                if (risk.total_service_locations_in_dataset !== undefined) {
                    addField(
                        riskSection,
                        "Total Locations in Dataset",
                        formatNumber(
                            risk.total_service_locations_in_dataset,
                            0
                        )
                    );
                }

                if (risk.percent_of_dataset_affected !== undefined) {
                    addField(
                        riskSection,
                        "Dataset Affected",
                        `${formatNumber(
                            risk.percent_of_dataset_affected
                        )}%`
                    );
                }

                if (risk.data_confidence || risk.confidence) {
                    addField(
                        riskSection,
                        "Data Confidence",
                        risk.data_confidence || risk.confidence
                    );
                }

                if (risk.method) {
                    addField(
                        riskSection,
                        "Calculation Method",
                        risk.method
                    );
                }

                if (risk.storm_scenario) {
                    addField(
                        riskSection,
                        "Assumed Storm Scenario",
                        formatLabel(risk.storm_scenario)
                    );
                }

                if (risk.storm_risk_score !== undefined) {
                    addField(
                        riskSection,
                        "Storm-Adjusted Score",
                        formatNumber(risk.storm_risk_score, 3)
                    );
                }

                if (risk.explanation) {
                    const explanation = createElement(
                        "p",
                        "analysis-explanation",
                        risk.explanation
                    );
                    riskSection.appendChild(explanation);
                }
            }
        }

        // Repair tasks
        const repairTasks = Array.isArray(details.repair_tasks)
            ? details.repair_tasks
            : [];

        if (repairTasks.length > 0) {
            const tasksSection = addSection(panel, "Assumed Repair Tasks");

            repairTasks.forEach((task, index) => {
                const taskCard = createElement("div", "repair-task");

                const taskName = task.connector_code
                    ? `Connector ${task.connector_code}`
                    : formatLabel(task.category || "Repair task");

                const taskHeading = createElement(
                    "strong",
                    "repair-task-title",
                    `${index + 1}. ${taskName}`
                );

                taskCard.appendChild(taskHeading);

                addField(
                    taskCard,
                    "Action",
                    formatLabel(task.action || "install")
                );

                if (task.size !== undefined && task.size !== null) {
                    addField(taskCard, "Size", `${task.size} kVA`);
                }

                addField(
                    taskCard,
                    "Quantity",
                    formatNumber(task.quantity ?? 1, 0)
                );

                tasksSection.appendChild(taskCard);
            });
        }

        // Labor estimate
        if (labor && typeof labor === "object") {
            const laborSection = addSection(panel, "Labor Estimate");

            if (labor.error) {
                addField(laborSection, "Status", labor.error);
            } else if (labor.total_labor_hours !== undefined) {
                addField(
                    laborSection,
                    "Total Labor",
                    `${formatNumber(labor.total_labor_hours)} labor-hours`
                );

                if (Array.isArray(labor.tasks)) {
                    labor.tasks.forEach(task => {
                        const taskName = task.code
                            ? `${task.category || "Task"} (${task.code})`
                            : formatLabel(task.category || "Task");

                        const action = formatLabel(task.action || "install");

                        addField(
                            laborSection,
                            `${taskName} - ${action}`,
                            `${formatNumber(task.total_labor_hours)} hours`
                        );
                    });
                }

                if (labor.note) {
                    const note = createElement(
                        "p",
                        "analysis-note",
                        labor.note
                    );
                    laborSection.appendChild(note);
                }
            } else {
                addField(
                    laborSection,
                    "Status",
                    "No labor estimate available"
                );
            }
        } else if (details.equipment_id) {
            const laborSection = addSection(panel, "Labor Estimate");

            addField(
                laborSection,
                "Status",
                "No labor estimate available for this identifier"
            );
        }

        // Assumptions and limitations
        const assumptions = Array.isArray(details.assumptions)
            ? details.assumptions
            : [];

        if (assumptions.length > 0) {
            const assumptionsSection = addSection(panel, "Assumptions");

            const list = createElement("ul", "analysis-assumptions");

            assumptions.forEach(assumption => {
                list.appendChild(
                    createElement("li", "", assumption)
                );
            });

            assumptionsSection.appendChild(list);
        }

        // Only append the panel if it has meaningful information.
        chatMessages.appendChild(panel);
        scrollToLatest();

        return panel;
    }

    // --------------------------------------------------
    // Loading / button state
    // --------------------------------------------------

    function setLoading(isLoading) {
        if (sendButton) {
            sendButton.disabled = isLoading;

            if (!sendButton.dataset.originalText) {
                sendButton.dataset.originalText =
                    sendButton.textContent.trim() || "Send";
            }

            sendButton.textContent = isLoading
                ? "Thinking..."
                : sendButton.dataset.originalText;
        }

        messageInput.disabled = isLoading;
        chatForm.setAttribute("aria-busy", String(isLoading));
    }

    // --------------------------------------------------
    // Send a message to Flask
    // --------------------------------------------------

    async function sendMessage(message) {
        const trimmedMessage = message.trim();

        if (!trimmedMessage) {
            return;
        }

        appendMessage("user", trimmedMessage);
        messageInput.value = "";
        setLoading(true);

        const loadingMessage = appendMessage(
            "assistant",
            "Analyzing your request..."
        );

        try {
            const response = await fetch("/chat", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    message: trimmedMessage,
                }),
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error || `Request failed (${response.status})`
                );
            }

            // Replace the temporary loading message.
            loadingMessage.remove();

            const reply =
                typeof data.reply === "string" && data.reply.trim()
                    ? data.reply
                    : "I couldn't generate a response for that request.";

            appendMessage("assistant", reply);

            // The backend returns analysis data under data.details.
            if (data.details) {
                renderAnalysis(data.details);
            }

            if (data.error) {
                appendMessage(
                    "assistant",
                    `Additional information: ${data.error}`
                );
            }
        } catch (error) {
            loadingMessage.remove();

            appendMessage(
                "assistant",
                `Sorry, I couldn't process that request. ${error.message}`
            );

            console.error("GridAssist chat error:", error);
        } finally {
            setLoading(false);
            messageInput.focus();
            scrollToLatest();
        }
    }

    // --------------------------------------------------
    // Form submission
    // --------------------------------------------------

    chatForm.addEventListener("submit", event => {
        event.preventDefault();

        if (messageInput.disabled) {
            return;
        }

        sendMessage(messageInput.value);
    });

    // Optional support for an existing send button outside the form.
    if (sendButton && sendButton.form !== chatForm) {
        sendButton.addEventListener("click", event => {
            event.preventDefault();

            if (!messageInput.disabled) {
                sendMessage(messageInput.value);
            }
        });
    }

    // Enter sends a message; Shift+Enter adds a newline in textareas.
    messageInput.addEventListener("keydown", event => {
        if (
            event.key === "Enter" &&
            !event.shiftKey &&
            messageInput.tagName.toLowerCase() === "textarea"
        ) {
            event.preventDefault();
            chatForm.requestSubmit();
        }
    });

    console.log("GridAssist chat initialized.");
});
