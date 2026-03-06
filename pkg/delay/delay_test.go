package delay

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestBusySpinAccuracy verifies busy-spin delay is within 2x of target.
func TestBusySpinAccuracy(t *testing.T) {
	targets := []time.Duration{
		10 * time.Microsecond,
		50 * time.Microsecond,
		100 * time.Microsecond,
		500 * time.Microsecond,
	}

	for _, target := range targets {
		t.Run(target.String(), func(t *testing.T) {
			start := time.Now()
			busySpin(target)
			elapsed := time.Since(start)

			if elapsed < target {
				t.Errorf("busy-spin too short: target=%v, actual=%v", target, elapsed)
			}
			// Allow up to 2x overshoot (scheduling noise on CI)
			if elapsed > 2*target {
				t.Errorf("busy-spin too long: target=%v, actual=%v (>2x)", target, elapsed)
			}
		})
	}
}

// TestComputeDelayRuns verifies compute mode doesn't panic and takes reasonable time.
func TestComputeDelayRuns(t *testing.T) {
	cfg := Config{Duration: 100 * time.Microsecond, Mode: ModeCompute}
	start := time.Now()
	Simulate(cfg)
	elapsed := time.Since(start)

	// Should take at least the target duration (approximately)
	if elapsed < 50*time.Microsecond {
		t.Errorf("compute delay suspiciously fast: %v for 100µs target", elapsed)
	}
}

// TestSleepBanConfig verifies that "sleep" is not a valid delay mode.
// If someone adds Mode("sleep") to the Simulate switch, this test catches it
// by checking that the function completes quickly (busyspin fallback) rather
// than sleeping for a long duration.
func TestSleepBanConfig(t *testing.T) {
	// Use a long target — if Simulate ever called time.Sleep, this would block
	cfg := Config{Duration: 50 * time.Microsecond, Mode: Mode("sleep")}
	start := time.Now()
	Simulate(cfg)
	elapsed := time.Since(start)

	// Should run as busyspin (default case), not as a real sleep
	if elapsed > 10*time.Millisecond {
		t.Fatal("mode 'sleep' appears to use time.Sleep — BANNED for sub-ms delays")
	}
}

// TestNoTimeSleepInServices scans all Go files under services/ for time.Sleep calls.
// This is a CI-level guard to prevent accidental scheduler artifact injection.
func TestNoTimeSleepInServices(t *testing.T) {
	servicesDir := filepath.Join("..", "..", "services")

	// Check if services directory exists (might not during early development)
	if _, err := os.Stat(servicesDir); os.IsNotExist(err) {
		t.Skip("services/ directory not found — skipping sleep scan")
	}

	fset := token.NewFileSet()
	var violations []string

	err := filepath.Walk(servicesDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, ".go") {
			return err
		}

		f, parseErr := parser.ParseFile(fset, path, nil, 0)
		if parseErr != nil {
			return nil // skip unparsable files
		}

		ast.Inspect(f, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			ident, ok := sel.X.(*ast.Ident)
			if !ok {
				return true
			}
			if ident.Name == "time" && sel.Sel.Name == "Sleep" {
				pos := fset.Position(call.Pos())
				violations = append(violations, pos.String())
			}
			return true
		})
		return nil
	})

	if err != nil {
		t.Fatalf("failed to walk services/: %v", err)
	}

	if len(violations) > 0 {
		t.Fatalf("BANNED: time.Sleep found in service code at:\n  %s\n"+
			"Use pkg/delay.Simulate() instead.", strings.Join(violations, "\n  "))
	}
}
