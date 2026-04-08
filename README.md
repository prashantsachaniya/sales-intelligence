# Sales Intelligence Automator

AI-powered lead research prototype for sales teams.  
This project accepts lead inputs such as website URLs, company names, and imperfect business descriptions, performs lightweight web research, and generates a structured sales brief using Gemini.

This version is tailored for **Moksh Tech**, so the qualification and sales-question logic is designed from Moksh Tech’s sales perspective.

---

## Overview

Sales teams often spend significant time researching leads before discovery calls. This project automates that process by:

- accepting lead inputs
- resolving or visiting company websites
- scraping and cleaning relevant website content
- sending structured context to Gemini
- generating a concise sales brief for each lead

The output for each lead includes:

- **Company Overview**
- **Core Product or Service**
- **Target Customer or Audience**
- **B2B Qualified** – Yes / No
- **Qualification Reason**
- **Three Sales Questions** tailored for Moksh Tech’s sales team

---

## Current Scope

This prototype supports:

- direct URL-based leads
- fuzzy company-name/location leads
- official website lookup for non-URL leads
- basic internal page crawling
- HTML cleanup and boilerplate filtering
- Gemini-powered structured analysis
- simple web UI for manual testing
- deployment through **Google Cloud Run Functions UI**

This is a prototype built for interview/demo purposes, with emphasis on architecture, reasoning, and practical implementation.

---

## Example Inputs

Examples of supported lead inputs:

```text
https://www.houstonroofingonline.com
BrightPlay Turf – Artificial Turf & Landscaping, Chicago IL
https://www.springhilllandscaping.com
Joe's Backyard Landscaping – Phoenix AZ
