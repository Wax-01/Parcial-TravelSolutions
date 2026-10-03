import { gql } from "@apollo/client";

export const ME = gql`
  query Me { me { id email } }
`;

// Solo se piden los campos que la UI muestra: el Gateway proyecta exactamente esas columnas hasta la BD.
// source/fetchedAt: de qué scraper viene cada dato (Booking.com, Google Flights, KAYAK o la fuente sintética).
export const SEARCH_PACKAGES = gql`
  query Buscar($origin: String!, $destination: String!, $date: String!, $nights: Int!, $realOnly: Boolean!) {
    searchPackages(origin: $origin, destination: $destination, departDate: $date, nights: $nights, first: 5, realOnly: $realOnly) {
      flights { edges { node { id airline departureAt price seatsAvailable source } } }
      hotels { edges { node { id name stars pricePerNight source } } }
      cars { edges { node { id provider model pricePerDay source } } }
    }
  }
`;

// Paquetes destacados de la portada: la opción más barata (solo datos reales) por destino, en una sola petición.
const featuredFields = `
  flights { edges { node { id airline price source } } }
  hotels { edges { node { id name pricePerNight source } } }
  cars { edges { node { id model pricePerDay source } } }
`;
export const FEATURED = gql`
  query Destacados($date: String!, $nights: Int!) {
    ctg: searchPackages(origin: "BOG", destination: "CTG", departDate: $date, nights: $nights, first: 1, realOnly: true) { ${featuredFields} }
    mde: searchPackages(origin: "BOG", destination: "MDE", departDate: $date, nights: $nights, first: 1, realOnly: true) { ${featuredFields} }
    mia: searchPackages(origin: "BOG", destination: "MIA", departDate: $date, nights: $nights, first: 1, realOnly: true) { ${featuredFields} }
    mad: searchPackages(origin: "BOG", destination: "MAD", departDate: $date, nights: $nights, first: 1, realOnly: true) { ${featuredFields} }
  }
`;

// Resumen de la ingesta (Prefect + Dask + scrapers) por tipo y fuente.
export const DATA_SOURCES = gql`
  query Fuentes { dataSources { id kind source items lastFetchedAt minPrice } }
`;

export const LOGIN = gql`
  mutation Login($email: String!, $password: String!) {
    login(email: $email, password: $password) {
      __typename
      ... on User { id email }
      ... on ApiError { code message }
    }
  }
`;

export const REGISTER = gql`
  mutation Registro($email: String!, $password: String!) {
    register(email: $email, password: $password) {
      __typename
      ... on User { id email }
      ... on ApiError { code message }
    }
  }
`;

export const LOGOUT = gql`
  mutation Logout { logout }
`;

export const CREATE_BOOKING = gql`
  mutation Reservar($input: CreateBookingInput!) {
    createBooking(input: $input) {
      __typename
      ... on BookingAccepted { orderId status idempotent }
      ... on ApiError { code message }
    }
  }
`;

export const ORDER = gql`
  query Orden($id: UUID!) {
    order(id: $id) {
      id status totalAmount currency failureReason
      steps { id step action status detail }
    }
  }
`;

export const MY_ORDERS = gql`
  query MisOrdenes { myOrders(first: 8) { id status totalAmount createdAt failureReason } }
`;
