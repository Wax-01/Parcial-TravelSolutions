import React from "react";
import { createRoot } from "react-dom/client";
import { ApolloClient, ApolloProvider, HttpLink, InMemoryCache } from "@apollo/client";
import App from "./App.jsx";
import "./styles.css";

// Toda la comunicación con el backend pasa por el API Gateway GraphQL (único endpoint).
const client = new ApolloClient({
  link: new HttpLink({ uri: "/graphql", credentials: "same-origin" }),
  cache: new InMemoryCache(),
  defaultOptions: { query: { fetchPolicy: "network-only" }, watchQuery: { fetchPolicy: "network-only" } },
});

createRoot(document.getElementById("root")).render(
  <ApolloProvider client={client}>
    <App />
  </ApolloProvider>
);
