window.APP_CONFIG = {
  apiUrl: "https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com",
  cognitoDomain: "https://YOUR_DOMAIN.auth.us-east-1.amazoncognito.com",
  cognitoClientId: "YOUR_COGNITO_APP_CLIENT_ID",
  redirectUri: window.location.origin + window.location.pathname,
  logoutUri: window.location.origin + window.location.pathname
};
