export default function ErrorState({ message = "Couldn't load this right now. Please try again shortly." }: { message?: string }) {
  return <p className="errorState" role="alert">{message}</p>;
}
