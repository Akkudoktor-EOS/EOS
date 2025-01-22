ERROR_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Energy Optimization System (EOS) Error</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background-color: #f5f5f5;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }
        .error-container {
            background: white;
            padding: 2rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            max-width: 500px;
            width: 100%;
            text-align: center;
        }
        .error-code {
            font-size: 4rem;
            font-weight: bold;
            color: #e53e3e;
            margin: 0;
        }
        .error-title {
            font-size: 1.5rem;
            color: #2d3748;
            margin: 1rem 0;
        }
        .error-message {
            color: #4a5568;
            margin-bottom: 1.5rem;
        }
        .error-details {
            background: #f7fafc;
            padding: 1rem;
            border-radius: 4px;
            margin-bottom: 1.5rem;
            text-align: left;
            font-family: monospace;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .back-button {
            background: #3182ce;
            color: white;
            border: none;
            padding: 0.75rem 1.5rem;
            border-radius: 4px;
            text-decoration: none;
            display: inline-block;
            transition: background-color 0.2s;
        }
        .back-button:hover {
            background: #2c5282;
        }
    </style>
</head>
<body>
    <div class="error-container">
        <h1 class="error-code">STATUS_CODE</h1>
        <h2 class="error-title">ERROR_TITLE</h2>
        <p class="error-message">ERROR_MESSAGE</p>
        <div class="error-details">ERROR_DETAILS</div>
        <a href="/docs" class="back-button">Back to Home</a>
    </div>
</body>
</html>
"""


def create_error_page(
    status_code: str, error_title: str, error_message: str, error_details: str
) -> str:
    """Create an error page by replacing placeholders in the template."""
    return (
        ERROR_PAGE_TEMPLATE.replace("STATUS_CODE", status_code)
        .replace("ERROR_TITLE", error_title)
        .replace("ERROR_MESSAGE", error_message)
        .replace("ERROR_DETAILS", error_details)
    )
