import { gql } from "@apollo/client";

export const ME = gql`
  query Me { me { id email } }
`;

// Solo se piden los campos que la UI muestra: el Gateway proyecta exactamente esas columnas hasta la BD.
export const SEARCH_PACKAGES = gql`
  query Buscar($origin: String!, $destination: String!, $date: String!, $nights: Int!) {
    searchPackages(origin: $origin, destination: $destination, departDate: $date, nights: $nights, first: 5) {
      flights { edges { node { id airline departureAt price seatsAvailable } } }
      hotels { edges { node { id name stars pricePerNight } } }
      cars { edges { node { id provider model pricePerDay } } }
    }
  }
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
