import React, { Component, ErrorInfo, ReactNode } from "react";
import { View, Text, ScrollView, TouchableOpacity, StyleSheet } from "react-native";

// Error boundary at the app root. Without it, any uncaught render error in
// a sub-component white-screens the whole app and the user has to force-
// quit. The boundary catches the error, shows a fallback, and gives the
// user a button to retry — most errors clear after a remount.

type Props = { children: ReactNode };
type State = { error: Error | null; info: ErrorInfo | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info);
    this.setState({ info });
  }

  reset = () => this.setState({ error: null, info: null });

  render() {
    if (!this.state.error) return this.props.children;

    // Inline styles only — we can't trust the theme module to be intact at
    // the moment of a render error. Match the dark palette manually.
    const stack =
      (this.state.error.message || "") +
      (this.state.info?.componentStack ? "\n" + this.state.info.componentStack : "");

    return (
      <View style={styles.root}>
        <Text style={styles.label}>Something went wrong</Text>
        <Text style={styles.title}>
          The app hit an unexpected error and stopped rendering.
        </Text>
        <ScrollView style={styles.stack}>
          <Text style={styles.stackText}>{stack}</Text>
        </ScrollView>
        <TouchableOpacity onPress={this.reset} style={styles.button}>
          <Text style={styles.buttonText}>Try again</Text>
        </TouchableOpacity>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#07090e",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    gap: 14,
  },
  label: {
    fontSize: 11,
    letterSpacing: 2,
    color: "#ff6b6b",
    textTransform: "uppercase",
    fontFamily: "monospace",
  },
  title: {
    fontSize: 18,
    fontWeight: "600",
    color: "#e6e7ea",
    textAlign: "center",
    maxWidth: 320,
  },
  stack: {
    maxHeight: 220,
    width: "100%",
    backgroundColor: "#10131a",
    borderRadius: 8,
    borderWidth: 0.5,
    borderColor: "#1f242c",
    padding: 12,
  },
  stackText: {
    color: "#9ba1a8",
    fontSize: 11,
    fontFamily: "monospace",
  },
  button: {
    paddingHorizontal: 22,
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 0.5,
    borderColor: "#4d8af0",
    backgroundColor: "#1a3060",
    marginTop: 6,
  },
  buttonText: {
    color: "#9bbdff",
    fontSize: 12,
    fontWeight: "500",
    letterSpacing: 1,
    textTransform: "uppercase",
    fontFamily: "monospace",
  },
});
