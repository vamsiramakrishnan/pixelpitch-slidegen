terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.28.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}
